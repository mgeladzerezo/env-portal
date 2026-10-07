import pytest

from portal.queue import CeleryTaskQueue
from portal.state_machine import EnvironmentStatus
from portal.worker import tasks
from tests.conftest import Portal


@pytest.fixture
def eager_celery(portal: Portal, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run Celery tasks in-process so the task wiring is exercised without a broker."""
    monkeypatch.setattr(tasks, "get_container", lambda: portal.container)
    monkeypatch.setattr(tasks.celery_app.conf, "task_always_eager", True)


def test_run_operation_task_executes_the_operation(portal: Portal, eager_celery: None) -> None:
    environment_id = portal.request()
    (operation_id,) = portal.queue.pending

    CeleryTaskQueue().enqueue(operation_id)

    assert portal.env(environment_id).status is EnvironmentStatus.READY


def test_maintenance_task_reaps_expired_environments(portal: Portal, eager_celery: None) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)

    result = tasks.maintenance.delay().get()
    portal.run_pending()

    assert result == {"destroyed": 1, "deferred": 0, "gave_up": 0}
    assert portal.env(environment_id).status is EnvironmentStatus.DESTROYED


def test_maintenance_is_scheduled_by_beat() -> None:
    schedule = tasks.celery_app.conf.beat_schedule["maintenance"]

    assert schedule["task"] == "portal.maintenance"
    assert tasks.celery_app.conf.task_acks_late is True
