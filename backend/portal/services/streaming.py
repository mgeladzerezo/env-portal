import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass

from starlette.concurrency import run_in_threadpool

from portal.db import SessionFactory
from portal.models import LogLine
from portal.services import environments
from portal.services.logs import read_logs

BATCH_SIZE = 500
KEEPALIVE_EVERY_POLLS = 30


@dataclass(frozen=True)
class Snapshot:
    status: str
    active: bool
    lines: list[LogLine]


def read_snapshot(sessions: SessionFactory, environment_id: str, after: int) -> Snapshot:
    with sessions() as session:
        # Order matters. Workers commit log lines before they commit the end of the
        # operation, so reading "is it still active" first means that when the answer
        # is no, the log query that follows sees every line the operation wrote.
        environment = environments.get_environment(session, environment_id)
        active = environment.active_operation is not None
        status = environment.status.value
        session.commit()
        lines = read_logs(session, environment_id, after=after, limit=BATCH_SIZE)
    return Snapshot(status=status, active=active, lines=lines)


def format_event(event: str, data: dict[str, object], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data)}\n\n"


async def environment_events(
    read: Callable[[int], Snapshot],
    after: int = 0,
    poll_interval: float = 0.5,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> AsyncIterator[str]:
    """Server-sent events for one environment: log lines, status changes, then "end".

    Log events carry the line's seq as the SSE id, so a browser that reconnects sends
    Last-Event-ID and resumes exactly where it stopped. The stream ends once no
    operation is active and every line has been sent.
    """
    last_seq = after
    last_status: str | None = None
    idle_polls = 0
    while True:
        snapshot = await run_in_threadpool(read, last_seq)
        for line in snapshot.lines:
            last_seq = line.seq
            yield format_event(
                "log",
                {
                    "seq": line.seq,
                    "operation_id": line.operation_id,
                    "message": line.message,
                    "created_at": line.created_at.isoformat(),
                },
                event_id=line.seq,
            )
        if snapshot.status != last_status:
            last_status = snapshot.status
            yield format_event("status", {"status": snapshot.status, "active": snapshot.active})

        drained = len(snapshot.lines) < BATCH_SIZE
        if not snapshot.active and drained:
            yield format_event("end", {"last_seq": last_seq})
            return
        if drained:
            idle_polls = 0 if snapshot.lines else idle_polls + 1
            if idle_polls >= KEEPALIVE_EVERY_POLLS:
                idle_polls = 0
                yield ": keepalive\n\n"
            await sleep(poll_interval)
