from portal.models import OperationKind, OperationStatus
from portal.policy.base import Violation
from portal.services.maintenance import REAPER, ReapReport
from portal.state_machine import EnvironmentStatus
from portal.terraform.base import TerraformCommandError
from tests.conftest import Portal

S = EnvironmentStatus


def destroy_operations(portal: Portal, environment_id: str) -> list:
    return [op for op in portal.operations(environment_id) if op.kind is OperationKind.DESTROY]


def test_environment_within_its_ttl_is_left_alone(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=4)
    portal.clock.advance(hours=3, minutes=59)

    report = portal.container.reaper.run_once()

    assert report == ReapReport()
    assert portal.env(environment_id).status is S.READY


def test_expired_environment_is_destroyed(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=4)
    portal.clock.advance(hours=4, seconds=1)

    report = portal.container.reaper.run_once()
    portal.run_pending()

    assert report.destroyed == [environment_id]
    environment = portal.env(environment_id)
    assert environment.status is S.DESTROYED
    assert environment.last_operation.requested_by == REAPER
    assert portal.runner.state[portal.state_key(environment_id)] == []


def test_environment_expires_exactly_at_its_deadline(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=1)

    assert portal.container.reaper.run_once().destroyed == [environment_id]


def test_only_expired_environments_are_reaped(portal: Portal) -> None:
    short = portal.ready(name="short", ttl_hours=1)
    long = portal.ready(name="long", ttl_hours=24)
    portal.clock.advance(hours=2)

    report = portal.container.reaper.run_once()
    portal.run_pending()

    assert report.destroyed == [short]
    assert portal.env(short).status is S.DESTROYED
    assert portal.env(long).status is S.READY


def test_environment_that_expires_mid_apply_is_deferred_then_reaped(portal: Portal) -> None:
    environment_id = portal.request(ttl_hours=1)
    mid_apply: list[ReapReport] = []

    def expire_during_apply(_workspace) -> None:
        portal.clock.advance(hours=2)
        assert portal.env(environment_id).status is S.APPLYING
        mid_apply.append(portal.container.reaper.run_once())

    portal.runner.hooks["apply"] = expire_during_apply
    portal.run_pending()

    assert mid_apply[0].deferred == [environment_id]
    assert mid_apply[0].destroyed == []
    assert destroy_operations(portal, environment_id) == []
    assert portal.env(environment_id).status is S.READY

    report = portal.container.reaper.run_once()
    portal.run_pending()

    assert report.destroyed == [environment_id]
    assert portal.env(environment_id).status is S.DESTROYED


def test_environment_that_expires_mid_plan_is_deferred(portal: Portal) -> None:
    environment_id = portal.request(ttl_hours=1)
    mid_plan: list[ReapReport] = []

    def expire_during_plan(_workspace) -> None:
        portal.clock.advance(hours=2)
        mid_plan.append(portal.container.reaper.run_once())

    portal.runner.hooks["plan"] = expire_during_plan
    portal.run_pending()

    assert mid_plan[0].deferred == [environment_id]
    assert portal.runner.commands().count("destroy") == 0


def test_expired_environment_still_waiting_in_the_queue_is_deferred(portal: Portal) -> None:
    environment_id = portal.request(ttl_hours=1)
    portal.clock.advance(hours=2)

    report = portal.container.reaper.run_once()

    assert report.deferred == [environment_id]
    assert len(portal.operations(environment_id)) == 1


def test_reaper_does_not_queue_a_second_destroy_while_one_is_pending(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)

    portal.container.reaper.run_once()
    second = portal.container.reaper.run_once()

    assert second.destroyed == []
    assert len(destroy_operations(portal, environment_id)) == 1


def test_environment_mid_destroy_is_ignored(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)
    during_destroy: list[ReapReport] = []
    portal.runner.hooks["destroy"] = lambda ws: during_destroy.append(
        portal.container.reaper.run_once()
    )

    portal.container.reaper.run_once()
    portal.run_pending()

    assert during_destroy == [ReapReport()]
    assert len(destroy_operations(portal, environment_id)) == 1


def test_destroyed_environment_is_never_reaped_again(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)
    portal.container.reaper.run_once()
    portal.run_pending()

    portal.clock.advance(days=30)
    assert portal.container.reaper.run_once() == ReapReport()
    assert len(destroy_operations(portal, environment_id)) == 1


def test_expired_failed_environment_is_cleaned_up(portal: Portal) -> None:
    portal.runner.apply_fails_after = 1
    environment_id = portal.ready(ttl_hours=1)
    assert portal.env(environment_id).status is S.FAILED
    portal.clock.advance(hours=2)

    portal.container.reaper.run_once()
    portal.run_pending()

    assert portal.env(environment_id).status is S.DESTROYED
    assert portal.runner.state[portal.state_key(environment_id)] == []


def test_expired_blocked_environment_is_cleaned_up(portal: Portal) -> None:
    portal.policy.violations = [Violation("required-tags", "fake.network", "missing tags")]
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)

    portal.container.reaper.run_once()
    portal.run_pending()

    assert portal.env(environment_id).status is S.DESTROYED


def test_reaper_stops_retrying_a_destroy_that_keeps_failing(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)
    portal.runner.failures["destroy"] = [
        TerraformCommandError("destroy", 1, ["Error: DependencyViolation"]) for _ in range(5)
    ]

    for _ in range(3):
        assert portal.container.reaper.run_once().destroyed == [environment_id]
        portal.run_pending()
    report = portal.container.reaper.run_once()

    assert report.gave_up == [environment_id]
    assert report.destroyed == []
    attempts = destroy_operations(portal, environment_id)
    assert len(attempts) == 3
    assert all(op.status is OperationStatus.FAILED for op in attempts)
    assert portal.env(environment_id).status is S.FAILED


def test_a_person_can_still_destroy_after_the_reaper_gave_up(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)
    portal.runner.failures["destroy"] = [
        TerraformCommandError("destroy", 1, ["Error: DependencyViolation"]) for _ in range(3)
    ]
    for _ in range(3):
        portal.container.reaper.run_once()
        portal.run_pending()

    portal.destroy(environment_id)
    portal.run_pending()

    assert portal.env(environment_id).status is S.DESTROYED


def test_maintenance_pass_reaps_expired_environments(portal: Portal) -> None:
    environment_id = portal.ready(ttl_hours=1)
    portal.clock.advance(hours=2)

    report = portal.container.run_maintenance()
    portal.run_pending()

    assert report.destroyed == [environment_id]
    assert portal.env(environment_id).status is S.DESTROYED
