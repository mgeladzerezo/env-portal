from celery import Celery

from portal.config import get_settings
from portal.container import get_container

settings = get_settings()

celery_app = Celery("portal", broker=settings.celery_broker_url)
celery_app.conf.update(
    task_ignore_result=True,
    # Acknowledge only after the task returns, so a worker that dies mid-operation gets
    # its task redelivered. The executor's claim step turns that redelivery into a no-op
    # and the maintenance job fails the abandoned operation.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # One Terraform run per worker process at a time.
    worker_prefetch_multiplier=1,
    # Redis redelivers unacknowledged tasks after this long; keep it above the longest
    # Terraform command so a slow apply is not handed to a second worker.
    broker_transport_options={"visibility_timeout": settings.command_timeout_seconds * 2},
    beat_schedule={
        "maintenance": {
            "task": "portal.maintenance",
            "schedule": float(settings.maintenance_interval_seconds),
        }
    },
)


@celery_app.task(name="portal.run_operation")
def run_operation(operation_id: int) -> None:
    get_container().executor.run(operation_id)


@celery_app.task(name="portal.maintenance")
def maintenance() -> dict[str, int]:
    report = get_container().run_maintenance()
    return {
        "destroyed": len(report.destroyed),
        "deferred": len(report.deferred),
        "gave_up": len(report.gave_up),
    }
