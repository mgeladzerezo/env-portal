from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from portal.clock import Clock
from portal.db import SessionFactory
from portal.models import LogLine, Operation, OperationStatus

HEARTBEAT_INTERVAL = timedelta(seconds=10)
_MAX_SEQ_RETRIES = 3


def last_seq(session: Session, environment_id: str) -> int:
    return session.scalar(
        select(func.coalesce(func.max(LogLine.seq), 0)).where(
            LogLine.environment_id == environment_id
        )
    )


def read_logs(
    session: Session, environment_id: str, after: int = 0, limit: int = 500
) -> list[LogLine]:
    return list(
        session.scalars(
            select(LogLine)
            .where(LogLine.environment_id == environment_id, LogLine.seq > after)
            .order_by(LogLine.seq)
            .limit(limit)
        )
    )


class LogWriter:
    """Appends an operation's output, one committed row per line.

    Committing per line is what makes the output visible to the SSE endpoint while
    Terraform is still running. Each write also refreshes the operation heartbeat, so a
    long apply that is still printing is never mistaken for a dead worker.
    """

    def __init__(
        self, sessions: SessionFactory, clock: Clock, environment_id: str, operation_id: int
    ) -> None:
        self._sessions = sessions
        self._clock = clock
        self._environment_id = environment_id
        self._operation_id = operation_id
        self._last_heartbeat: datetime | None = None
        with sessions() as session:
            self._next_seq = last_seq(session, environment_id) + 1

    def write(self, message: str) -> None:
        now = self._clock.now()
        heartbeat_due = (
            self._last_heartbeat is None or now - self._last_heartbeat >= HEARTBEAT_INTERVAL
        )
        for _ in range(_MAX_SEQ_RETRIES):
            with self._sessions() as session:
                if heartbeat_due:
                    self._touch_heartbeat(session, now)
                session.add(
                    LogLine(
                        environment_id=self._environment_id,
                        operation_id=self._operation_id,
                        seq=self._next_seq,
                        message=message,
                        created_at=now,
                    )
                )
                try:
                    session.commit()
                except IntegrityError:
                    # Another writer took this seq. That only happens when a worker that
                    # was declared lost is still running; re-read the tail and continue.
                    session.rollback()
                    self._next_seq = last_seq(session, self._environment_id) + 1
                    continue
            self._next_seq += 1
            if heartbeat_due:
                self._last_heartbeat = now
            return
        raise RuntimeError(f"could not append log line for environment {self._environment_id}")

    def _touch_heartbeat(self, session: Session, now: datetime) -> None:
        session.execute(
            update(Operation)
            .where(Operation.id == self._operation_id, Operation.status == OperationStatus.RUNNING)
            .values(heartbeat_at=now)
        )
