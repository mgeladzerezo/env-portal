import logging
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select

from portal.clock import Clock
from portal.db import SessionFactory
from portal.errors import OperationInProgress
from portal.models import Environment, Operation, OperationKind, OperationStatus
from portal.queue import TaskQueue, dispatch
from portal.services import environments
from portal.state_machine import EnvironmentStatus, can_transition

logger = logging.getLogger(__name__)

S = EnvironmentStatus
REAPER = "reaper"


@dataclass
class ReapReport:
    destroyed: list[str] = field(default_factory=list)
    # Expired, but an operation is in flight; picked up on a later pass.
    deferred: list[str] = field(default_factory=list)
    # Expired, but automatic destroys keep failing; needs a person.
    gave_up: list[str] = field(default_factory=list)


class Reaper:
    """Queues a destroy for every environment whose TTL has passed.

    It never interrupts a running operation: destroying under a live apply would fight
    over the same state. Such environments are deferred and reaped once they settle.
    """

    def __init__(
        self,
        sessions: SessionFactory,
        queue: TaskQueue,
        clock: Clock,
        max_destroy_attempts: int = 3,
    ) -> None:
        self._sessions = sessions
        self._queue = queue
        self._clock = clock
        self._max_destroy_attempts = max_destroy_attempts

    def run_once(self) -> ReapReport:
        report = ReapReport()
        for environment_id in self._expired_ids():
            self._reap(environment_id, report)
        return report

    def _expired_ids(self) -> list[str]:
        with self._sessions() as session:
            return list(
                session.scalars(
                    select(Environment.id)
                    .where(
                        Environment.expires_at <= self._clock.now(),
                        Environment.status.not_in((S.DESTROYING, S.DESTROYED)),
                    )
                    .order_by(Environment.expires_at)
                )
            )

    def _reap(self, environment_id: str, report: ReapReport) -> None:
        with self._sessions() as session:
            environment = environments.get_environment(session, environment_id)
            if environment.active_operation is not None or not can_transition(
                environment.status, S.DESTROYING
            ):
                report.deferred.append(environment_id)
                return
            if self._failed_attempts(environment) >= self._max_destroy_attempts:
                report.gave_up.append(environment_id)
                return
            try:
                operation = environments.request_destroy(session, environment, REAPER, self._clock)
            except OperationInProgress:
                report.deferred.append(environment_id)
                return
        if operation is not None:
            dispatch(self._queue, operation.id)
            report.destroyed.append(environment_id)

    def _failed_attempts(self, environment: Environment) -> int:
        return sum(
            1
            for operation in environment.operations
            if operation.kind is OperationKind.DESTROY
            and operation.requested_by == REAPER
            and operation.status is OperationStatus.FAILED
        )


def recover_stale_operations(
    sessions: SessionFactory, clock: Clock, stale_after: timedelta
) -> list[int]:
    """Fail running operations whose worker stopped reporting.

    The environment moves to failed, from where it can be retried or destroyed. If the
    worker died holding the Terraform state lock, the next run reports that lock.
    """
    now = clock.now()
    recovered = []
    with sessions() as session:
        stale = session.scalars(
            select(Operation)
            .where(
                Operation.status == OperationStatus.RUNNING,
                Operation.heartbeat_at < now - stale_after,
            )
            .with_for_update(skip_locked=True)
        )
        for operation in stale:
            message = f"worker stopped responding during {operation.kind.value}"
            operation.status = OperationStatus.FAILED
            operation.error_code = "worker_lost"
            operation.error = message
            operation.finished_at = now
            environment = operation.environment
            if operation.kind is not OperationKind.UNLOCK and can_transition(
                environment.status, S.FAILED
            ):
                environment.status = S.FAILED
                environment.error = message
                environment.updated_at = now
            recovered.append(operation.id)
        session.commit()
    return recovered


def requeue_stuck_operations(
    sessions: SessionFactory, queue: TaskQueue, clock: Clock, queued_for: timedelta
) -> list[int]:
    """Re-send operations that have been queued for too long.

    Covers a broker that was down when the operation was created. Sending a task twice
    is safe because the executor claims the row before doing anything.
    """
    with sessions() as session:
        stuck = list(
            session.scalars(
                select(Operation.id).where(
                    Operation.status == OperationStatus.QUEUED,
                    Operation.created_at < clock.now() - queued_for,
                )
            )
        )
    for operation_id in stuck:
        dispatch(queue, operation_id)
    return stuck


def count_overdue(sessions: SessionFactory, clock: Clock) -> int:
    with sessions() as session:
        return session.scalar(
            select(func.count())
            .select_from(Environment)
            .where(Environment.expires_at <= clock.now(), Environment.status != S.DESTROYED)
        )
