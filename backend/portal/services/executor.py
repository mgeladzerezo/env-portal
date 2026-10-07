import logging
from dataclasses import dataclass
from typing import Any

from sqlalchemy import update
from sqlalchemy.orm import Session

from portal.catalog import EnvironmentSpec
from portal.clock import Clock
from portal.db import SessionFactory
from portal.errors import OperationSuperseded, PortalError
from portal.models import Environment, Operation, OperationKind, OperationStatus
from portal.policy.base import PolicyChecker
from portal.services.logs import LogWriter
from portal.state_machine import EnvironmentStatus, assert_transition, can_transition
from portal.terraform.base import StateLockedError, TerraformRunner
from portal.terraform.workspace import WorkspaceManager

logger = logging.getLogger(__name__)

S = EnvironmentStatus


@dataclass
class Job:
    operation_id: int
    kind: OperationKind
    lock_id: str | None
    spec: EnvironmentSpec
    # Which step is running, so a failure can be reported as plan_failed, apply_failed, ...
    phase: str = "start"


class OperationExecutor:
    """Runs one queued operation to completion. This is the only code that drives Terraform.

    Three rules keep it safe under redelivery and crashes:
    - an operation is claimed with a conditional UPDATE, so a task delivered twice runs once;
    - no database transaction is held open while Terraform runs;
    - every status change first checks that the operation still belongs to this worker.
    """

    def __init__(
        self,
        sessions: SessionFactory,
        runner: TerraformRunner,
        policy: PolicyChecker,
        workspaces: WorkspaceManager,
        clock: Clock,
    ) -> None:
        self._sessions = sessions
        self._runner = runner
        self._policy = policy
        self._workspaces = workspaces
        self._clock = clock

    def run(self, operation_id: int) -> None:
        if not self._claim(operation_id):
            logger.info("operation %s is not queued (already claimed or finished)", operation_id)
            return
        job = self._load_job(operation_id)
        log = LogWriter(self._sessions, self._clock, job.spec.id, operation_id)
        handler = {
            OperationKind.PROVISION: self._provision,
            OperationKind.DESTROY: self._destroy,
            OperationKind.UNLOCK: self._unlock,
        }[job.kind]
        try:
            handler(job, log)
        except OperationSuperseded:
            logger.warning(
                "operation %s was taken over; leaving the environment alone", operation_id
            )
        except StateLockedError as exc:
            log.write(f"[portal] {exc}")
            log.write(
                "[portal] The lock was left in place. Release it explicitly once you are "
                "sure no other run is active."
            )
            self._fail(job, "state_locked", str(exc), lock_info=exc.lock.as_dict())
        except PortalError as exc:
            log.write(f"[portal] {job.kind.value} failed during {job.phase}: {exc}")
            self._fail(job, f"{job.phase}_failed", str(exc))
        except Exception as exc:
            logger.exception("operation %s crashed", operation_id)
            log.write(f"[portal] internal error during {job.phase}: {exc}")
            self._fail(job, "internal_error", f"{type(exc).__name__}: {exc}")

    def _provision(self, job: Job, log: LogWriter) -> None:
        job.phase = "plan"
        self._advance(job, S.PLANNING, error=None, policy_violations=[])
        workspace = self._workspaces.prepare(job.spec)
        log.write(f"[portal] planning {job.spec.template}@{job.spec.module_version}")
        self._runner.init(workspace, log.write)
        plan = self._runner.plan(workspace, log.write)

        job.phase = "policy"
        result = self._policy.check(plan.plan)
        if not result.passed:
            for violation in result.violations:
                log.write(f"[policy] {violation.rule}: {violation.resource}: {violation.message}")
            log.write("[portal] plan blocked by policy; nothing was applied")
            self._finish(
                job,
                OperationStatus.FAILED,
                S.BLOCKED_BY_POLICY,
                error_code="policy_blocked",
                policy_violations=[violation.as_dict() for violation in result.violations],
            )
            return
        log.write("[portal] policy check passed")

        job.phase = "apply"
        self._advance(job, S.APPLYING)
        self._runner.apply(workspace, log.write)
        outputs = self._runner.output(workspace)
        log.write("[portal] environment is ready")
        self._finish(job, OperationStatus.SUCCEEDED, S.READY, outputs=outputs, lock_info=None)

    def _destroy(self, job: Job, log: LogWriter) -> None:
        job.phase = "destroy"
        self._advance(job, S.DESTROYING, error=None)
        workspace = self._workspaces.prepare(job.spec)
        log.write("[portal] destroying environment")
        self._runner.init(workspace, log.write)
        self._runner.destroy(workspace, log.write)
        log.write("[portal] environment destroyed")
        self._finish(
            job,
            OperationStatus.SUCCEEDED,
            S.DESTROYED,
            outputs={},
            lock_info=None,
            destroyed_at=self._clock.now(),
        )
        self._workspaces.remove(job.spec.id)

    def _unlock(self, job: Job, log: LogWriter) -> None:
        job.phase = "unlock"
        workspace = self._workspaces.prepare(job.spec)
        log.write(f"[portal] releasing state lock {job.lock_id} at the request of a user")
        self._runner.init(workspace, log.write)
        self._runner.force_unlock(workspace, job.lock_id or "", log.write)
        self._finish(job, OperationStatus.SUCCEEDED, None, lock_info=None)

    def _claim(self, operation_id: int) -> bool:
        now = self._clock.now()
        with self._sessions() as session:
            claimed = session.execute(
                update(Operation)
                .where(Operation.id == operation_id, Operation.status == OperationStatus.QUEUED)
                .values(status=OperationStatus.RUNNING, started_at=now, heartbeat_at=now)
            ).rowcount
            session.commit()
        return claimed == 1

    def _load_job(self, operation_id: int) -> Job:
        with self._sessions() as session:
            operation = session.get_one(Operation, operation_id)
            environment = operation.environment
            return Job(
                operation_id=operation.id,
                kind=operation.kind,
                lock_id=operation.lock_id,
                spec=EnvironmentSpec(
                    id=environment.id,
                    name=f"{environment.name}-{environment.id[:8]}",
                    owner=environment.owner,
                    template=environment.template,
                    module_version=environment.module_version,
                    size=environment.size,
                    expires_at=environment.expires_at,
                ),
            )

    def _still_ours(self, session: Session, job: Job, **values: Any) -> bool:
        """Update the operation only if it is still running.

        On PostgreSQL this UPDATE also takes the row lock, so the recovery job cannot
        declare the operation lost between this check and the commit that follows it.
        """
        updated = session.execute(
            update(Operation)
            .where(Operation.id == job.operation_id, Operation.status == OperationStatus.RUNNING)
            .values(heartbeat_at=self._clock.now(), **values)
        ).rowcount
        return updated == 1

    def _advance(self, job: Job, target: EnvironmentStatus, **fields: Any) -> None:
        with self._sessions() as session:
            if not self._still_ours(session, job):
                raise OperationSuperseded(f"operation {job.operation_id} is no longer running")
            self._move(session, job, target, fields)
            session.commit()

    def _finish(
        self,
        job: Job,
        status: OperationStatus,
        target: EnvironmentStatus | None,
        *,
        error_code: str | None = None,
        **fields: Any,
    ) -> None:
        now = self._clock.now()
        with self._sessions() as session:
            if not self._still_ours(
                session, job, status=status, finished_at=now, error_code=error_code
            ):
                raise OperationSuperseded(f"operation {job.operation_id} is no longer running")
            self._move(session, job, target, fields)
            session.commit()

    def _move(
        self,
        session: Session,
        job: Job,
        target: EnvironmentStatus | None,
        fields: dict[str, Any],
    ) -> None:
        environment = session.get_one(Environment, job.spec.id)
        if target is not None:
            assert_transition(environment.status, target)
            environment.status = target
        for name, value in fields.items():
            setattr(environment, name, value)
        environment.updated_at = self._clock.now()

    def _fail(
        self, job: Job, error_code: str, message: str, lock_info: dict[str, str] | None = None
    ) -> None:
        now = self._clock.now()
        with self._sessions() as session:
            if not self._still_ours(
                session,
                job,
                status=OperationStatus.FAILED,
                finished_at=now,
                error_code=error_code,
                error=message,
            ):
                return
            environment = session.get_one(Environment, job.spec.id)
            # An unlock that fails changes nothing about the infrastructure, and a job
            # that never left its starting status (illegal transition) must not be
            # relabelled as failed.
            if job.kind is not OperationKind.UNLOCK and can_transition(
                environment.status, S.FAILED
            ):
                environment.status = S.FAILED
                environment.error = message
            if lock_info is not None:
                environment.lock_info = lock_info
            environment.updated_at = now
            session.commit()
