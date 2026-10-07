import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

logger = logging.getLogger(__name__)


class TaskQueue(Protocol):
    def enqueue(self, operation_id: int) -> None: ...


class CeleryTaskQueue:
    def enqueue(self, operation_id: int) -> None:
        from portal.worker.tasks import run_operation

        run_operation.delay(operation_id)


class InlineTaskQueue:
    """Runs operations on a small thread pool in the current process (local development)."""

    def __init__(self, run: Callable[[int], None], max_workers: int = 2) -> None:
        self._run = run
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="operation")

    def enqueue(self, operation_id: int) -> None:
        self._pool.submit(self._run, operation_id)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


def dispatch(queue: TaskQueue, operation_id: int) -> None:
    """Hand a committed operation to the queue.

    The operation row is the source of truth. If the broker is unreachable the row stays
    queued and the maintenance job re-sends it, so the failure is logged, not raised.
    """
    try:
        queue.enqueue(operation_id)
    except Exception:
        logger.exception("could not enqueue operation %s; maintenance will retry", operation_id)
