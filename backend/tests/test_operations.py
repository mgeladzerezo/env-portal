import threading
import time
from datetime import timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from portal.errors import LockMismatch, OperationInProgress
from portal.models import Operation, OperationKind, OperationStatus
from portal.policy.base import PolicyCheckError, Violation
from portal.services import environments
from portal.services.maintenance import recover_stale_operations, requeue_stuck_operations
from portal.state_machine import EnvironmentStatus, IllegalTransition
from portal.terraform.base import LockInfo, TerraformCommandError
from tests.conftest import USER, Portal

S = EnvironmentStatus
ALL_RESOURCES = ["network", "compute", "database"]
STUCK_LOCK = LockInfo(
    id="9db590f1-b6fe-c5f2-2678-8804f089deba",
    who="worker-7f9c@portal",
    operation="OperationTypeApply",
    created="2026-01-01 11:58:07 +0000 UTC",
)


def test_provision_reaches_ready_and_stores_outputs(portal: Portal) -> None:
    environment_id = portal.request()
    assert portal.env(environment_id).status is S.REQUESTED

    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.READY
    assert environment.outputs == {"resources": ALL_RESOURCES}
    assert environment.error is None
    assert portal.runner.commands() == ["init", "plan", "apply", "output"]
    (operation,) = environment.operations
    assert operation.kind is OperationKind.PROVISION
    assert operation.status is OperationStatus.SUCCEEDED
    assert operation.finished_at is not None


def test_status_is_planning_during_plan_and_applying_during_apply(portal: Portal) -> None:
    environment_id = portal.request()
    seen: dict[str, S] = {}
    portal.runner.hooks["plan"] = lambda ws: seen.update(plan=portal.env(environment_id).status)
    portal.runner.hooks["apply"] = lambda ws: seen.update(apply=portal.env(environment_id).status)

    portal.run_pending()

    assert seen == {"plan": S.PLANNING, "apply": S.APPLYING}


def test_each_environment_has_its_own_state_key(portal: Portal) -> None:
    first = portal.ready(name="one")
    second = portal.ready(name="two")

    keys = {key for _, key in portal.runner.calls}
    assert keys == {
        f"environments/{first}/terraform.tfstate",
        f"environments/{second}/terraform.tfstate",
    }


def test_destroy_removes_resources_and_clears_outputs(portal: Portal) -> None:
    environment_id = portal.ready()

    portal.destroy(environment_id)
    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.DESTROYED
    assert environment.outputs == {}
    assert environment.destroyed_at == portal.clock.now()
    assert portal.runner.state[portal.state_key(environment_id)] == []


def test_policy_violation_blocks_the_plan_before_anything_is_applied(portal: Portal) -> None:
    portal.policy.violations = [
        Violation("no-open-ingress", "aws_security_group.vm", "ingress from the whole internet")
    ]
    environment_id = portal.request()

    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.BLOCKED_BY_POLICY
    assert environment.policy_violations == [
        {
            "rule": "no-open-ingress",
            "resource": "aws_security_group.vm",
            "message": "ingress from the whole internet",
        }
    ]
    assert "apply" not in portal.runner.commands()
    assert portal.runner.state[portal.state_key(environment_id)] == []
    assert environment.last_operation.error_code == "policy_blocked"


def test_policy_checker_receives_the_plan_that_terraform_produced(portal: Portal) -> None:
    portal.ready()

    (plan,) = portal.policy.plans
    assert [change["address"] for change in plan["resource_changes"]] == [
        "fake.network",
        "fake.compute",
        "fake.database",
    ]


def test_blocked_environment_can_be_replanned_once_the_policy_passes(portal: Portal) -> None:
    portal.policy.violations = [Violation("required-tags", "fake.network", "missing tags: owner")]
    environment_id = portal.ready()
    portal.policy.violations = []

    portal.retry(environment_id)
    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.READY
    assert environment.policy_violations == []


def test_blocked_environment_can_be_destroyed(portal: Portal) -> None:
    portal.policy.violations = [Violation("required-tags", "fake.network", "missing tags: owner")]
    environment_id = portal.ready()

    portal.destroy(environment_id)
    portal.run_pending()

    assert portal.env(environment_id).status is S.DESTROYED


def test_a_broken_policy_checker_fails_closed(portal: Portal) -> None:
    portal.policy.error = PolicyCheckError("conftest exited with code 2")
    environment_id = portal.request()

    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.FAILED
    assert environment.last_operation.error_code == "policy_failed"
    assert "apply" not in portal.runner.commands()


def test_plan_failure_marks_the_environment_failed(portal: Portal) -> None:
    portal.runner.failures["plan"].append(
        TerraformCommandError("plan", 1, ["Error: Invalid value for variable"])
    )
    environment_id = portal.request()

    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.FAILED
    assert "Invalid value for variable" in environment.error
    assert environment.last_operation.error_code == "plan_failed"
    assert environment.last_operation.status is OperationStatus.FAILED


def test_failed_apply_keeps_what_was_created_and_a_retry_finishes_the_rest(
    portal: Portal,
) -> None:
    portal.runner.apply_fails_after = 1
    environment_id = portal.request()
    portal.run_pending()

    failed = portal.env(environment_id)
    assert failed.status is S.FAILED
    assert failed.last_operation.error_code == "apply_failed"
    assert failed.outputs == {}
    assert portal.runner.state[portal.state_key(environment_id)] == ["network"]

    portal.retry(environment_id)
    portal.run_pending()

    retried = portal.env(environment_id)
    assert retried.status is S.READY
    assert retried.error is None
    # Nothing was created twice: the retry planned against the existing state.
    assert portal.runner.state[portal.state_key(environment_id)] == ALL_RESOURCES
    assert [op.status for op in retried.operations] == [
        OperationStatus.FAILED,
        OperationStatus.SUCCEEDED,
    ]


def test_failed_apply_can_be_destroyed_instead_of_retried(portal: Portal) -> None:
    portal.runner.apply_fails_after = 2
    environment_id = portal.ready()
    assert portal.runner.state[portal.state_key(environment_id)] == ["network", "compute"]

    portal.destroy(environment_id)
    portal.run_pending()

    assert portal.env(environment_id).status is S.DESTROYED
    assert portal.runner.state[portal.state_key(environment_id)] == []


def test_retrying_a_failed_destroy_destroys_again(portal: Portal) -> None:
    environment_id = portal.ready()
    portal.runner.failures["destroy"].append(
        TerraformCommandError("destroy", 1, ["Error: DependencyViolation"])
    )
    portal.destroy(environment_id)
    portal.run_pending()
    failed = portal.env(environment_id)
    assert failed.status is S.FAILED
    assert failed.last_operation.error_code == "destroy_failed"

    operation = portal.retry(environment_id)
    portal.run_pending()

    assert operation.kind is OperationKind.DESTROY
    assert portal.env(environment_id).status is S.DESTROYED


def test_retry_is_rejected_unless_the_environment_failed_or_was_blocked(portal: Portal) -> None:
    environment_id = portal.ready()

    with pytest.raises(IllegalTransition):
        portal.retry(environment_id)


def test_stuck_state_lock_is_reported_and_never_forced(portal: Portal) -> None:
    environment_id = portal.request()
    portal.runner.locks[portal.state_key(environment_id)] = STUCK_LOCK

    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.FAILED
    assert environment.last_operation.error_code == "state_locked"
    assert environment.lock_info["id"] == STUCK_LOCK.id
    assert environment.lock_info["who"] == "worker-7f9c@portal"
    assert STUCK_LOCK.id in environment.error
    assert "force-unlock" not in portal.runner.commands()
    assert portal.runner.locks[portal.state_key(environment_id)] == STUCK_LOCK
    assert any(STUCK_LOCK.id in line.message for line in portal.logs(environment_id))


def test_retry_does_not_break_a_lock_that_is_still_held(portal: Portal) -> None:
    environment_id = portal.request()
    portal.runner.locks[portal.state_key(environment_id)] = STUCK_LOCK
    portal.run_pending()

    portal.retry(environment_id)
    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.FAILED
    assert environment.lock_info["id"] == STUCK_LOCK.id
    assert "force-unlock" not in portal.runner.commands()


def test_unlock_needs_the_id_of_the_recorded_lock(portal: Portal) -> None:
    environment_id = portal.request()
    portal.runner.locks[portal.state_key(environment_id)] = STUCK_LOCK
    portal.run_pending()

    with pytest.raises(LockMismatch):
        portal.unlock(environment_id, "some-other-lock")
    assert "force-unlock" not in portal.runner.commands()


def test_unlock_is_rejected_when_no_lock_was_reported(portal: Portal) -> None:
    environment_id = portal.ready()

    with pytest.raises(LockMismatch):
        portal.unlock(environment_id, STUCK_LOCK.id)


def test_explicit_unlock_then_retry_recovers_the_environment(portal: Portal) -> None:
    environment_id = portal.request()
    portal.runner.locks[portal.state_key(environment_id)] = STUCK_LOCK
    portal.run_pending()

    portal.unlock(environment_id, STUCK_LOCK.id)
    portal.run_pending()

    unlocked = portal.env(environment_id)
    assert unlocked.lock_info is None
    assert unlocked.status is S.FAILED
    assert unlocked.last_operation.kind is OperationKind.UNLOCK
    assert unlocked.last_operation.status is OperationStatus.SUCCEEDED

    portal.retry(environment_id)
    portal.run_pending()
    assert portal.env(environment_id).status is S.READY


def test_task_delivered_twice_runs_terraform_once(portal: Portal) -> None:
    environment_id = portal.request()
    (operation_id,) = portal.queue.pending

    portal.container.executor.run(operation_id)
    portal.container.executor.run(operation_id)

    assert portal.runner.commands().count("apply") == 1
    assert portal.env(environment_id).status is S.READY


def test_two_workers_racing_for_one_operation_do_not_overlap(portal: Portal) -> None:
    portal.request()
    (operation_id,) = portal.queue.pending
    portal.runner.hooks["apply"] = lambda ws: time.sleep(0.2)
    start = threading.Barrier(2)

    def work() -> None:
        start.wait(timeout=5)
        portal.container.executor.run(operation_id)

    workers = [threading.Thread(target=work) for _ in range(2)]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)

    assert portal.runner.commands().count("apply") == 1
    assert portal.runner.overlapping_commands == 0


def test_second_operation_is_rejected_while_one_is_in_flight(portal: Portal) -> None:
    environment_id = portal.request()

    with pytest.raises(OperationInProgress):
        portal.destroy(environment_id)
    assert len(portal.operations(environment_id)) == 1


def test_database_refuses_a_second_active_operation(portal: Portal) -> None:
    environment_id = portal.request()

    with portal.container.sessions() as session, pytest.raises(IntegrityError):
        session.add(
            Operation(
                environment_id=environment_id,
                kind=OperationKind.DESTROY,
                status=OperationStatus.QUEUED,
                requested_by=USER,
                created_at=portal.clock.now(),
            )
        )
        session.commit()


def test_concurrent_destroy_requests_result_in_one_operation(portal: Portal) -> None:
    environment_id = portal.ready()
    start = threading.Barrier(4)
    outcomes: list[int | str] = []

    def request_destroy() -> None:
        with portal.container.sessions() as session:
            environment = environments.get_environment(session, environment_id)
            start.wait(timeout=5)
            try:
                operation = environments.request_destroy(session, environment, USER, portal.clock)
                outcomes.append(operation.id)
            except OperationInProgress:
                outcomes.append("rejected")

    threads = [threading.Thread(target=request_destroy) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    destroys = [op for op in portal.operations(environment_id) if op.kind is OperationKind.DESTROY]
    assert len(destroys) == 1
    assert outcomes.count(destroys[0].id) == 1
    assert outcomes.count("rejected") == 3


def test_destroy_request_is_idempotent(portal: Portal) -> None:
    environment_id = portal.ready()

    first = portal.destroy(environment_id)
    second = portal.destroy(environment_id)
    assert second.id == first.id
    assert portal.queue.enqueued.count(first.id) == 1

    portal.run_pending()
    assert portal.destroy(environment_id) is None
    assert portal.runner.commands().count("destroy") == 1


def test_lost_worker_is_detected_and_its_late_result_is_discarded(portal: Portal) -> None:
    environment_id = portal.request()
    stale_after = timedelta(minutes=15)

    def worker_stalls(_workspace) -> None:
        # Seen from the database the worker went silent for an hour, so maintenance
        # fails the operation. The worker then wakes up and finishes its apply.
        portal.clock.advance(hours=1)
        recovered = recover_stale_operations(portal.container.sessions, portal.clock, stale_after)
        assert len(recovered) == 1

    portal.runner.hooks["apply"] = worker_stalls
    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.FAILED
    assert environment.last_operation.error_code == "worker_lost"
    assert environment.last_operation.status is OperationStatus.FAILED
    assert environment.outputs == {}

    del portal.runner.hooks["apply"]
    portal.retry(environment_id)
    portal.run_pending()
    assert portal.env(environment_id).status is S.READY
    assert portal.runner.state[portal.state_key(environment_id)] == ALL_RESOURCES


def test_an_operation_that_keeps_logging_is_not_declared_lost(portal: Portal) -> None:
    environment_id = portal.request()
    stale_after = timedelta(minutes=15)
    recovered: list[int] = []

    def slow_step(_workspace) -> None:
        portal.clock.advance(minutes=10)
        recovered.extend(
            recover_stale_operations(portal.container.sessions, portal.clock, stale_after)
        )

    portal.runner.hooks["apply_step"] = slow_step
    portal.run_pending()

    assert recovered == []
    assert portal.env(environment_id).status is S.READY


def test_operation_whose_enqueue_failed_is_sent_again_by_maintenance(portal: Portal) -> None:
    environment_id = portal.request()
    lost = portal.queue.pending.pop()
    portal.queue.enqueued.clear()
    queued_for = timedelta(minutes=5)

    assert (
        requeue_stuck_operations(portal.container.sessions, portal.queue, portal.clock, queued_for)
        == []
    )

    portal.clock.advance(minutes=6)
    assert requeue_stuck_operations(
        portal.container.sessions, portal.queue, portal.clock, queued_for
    ) == [lost]
    portal.run_pending()

    assert portal.env(environment_id).status is S.READY


def test_unexpected_exception_still_leaves_a_recoverable_environment(portal: Portal) -> None:
    portal.runner.failures["apply"].append(RuntimeError("disk full"))
    environment_id = portal.request()

    portal.run_pending()

    environment = portal.env(environment_id)
    assert environment.status is S.FAILED
    assert environment.last_operation.error_code == "internal_error"
    portal.retry(environment_id)
    portal.run_pending()
    assert portal.env(environment_id).status is S.READY
