from itertools import product

import pytest

from portal.state_machine import (
    TRANSITIONS,
    EnvironmentStatus,
    IllegalTransition,
    assert_transition,
    can_transition,
)

S = EnvironmentStatus

# Written out by hand on purpose: the test must not derive its expectations from the
# table it is checking.
ALLOWED = {
    (S.REQUESTED, S.PLANNING),
    (S.REQUESTED, S.DESTROYING),
    (S.PLANNING, S.APPLYING),
    (S.PLANNING, S.BLOCKED_BY_POLICY),
    (S.PLANNING, S.FAILED),
    (S.BLOCKED_BY_POLICY, S.PLANNING),
    (S.BLOCKED_BY_POLICY, S.DESTROYING),
    (S.APPLYING, S.READY),
    (S.APPLYING, S.FAILED),
    (S.READY, S.DESTROYING),
    (S.FAILED, S.PLANNING),
    (S.FAILED, S.DESTROYING),
    (S.DESTROYING, S.DESTROYED),
    (S.DESTROYING, S.FAILED),
}


@pytest.mark.parametrize(("current", "target"), list(product(S, S)))
def test_every_pair_of_states(current: S, target: S) -> None:
    if (current, target) in ALLOWED:
        assert can_transition(current, target)
        assert_transition(current, target)
    else:
        assert not can_transition(current, target)
        with pytest.raises(IllegalTransition) as raised:
            assert_transition(current, target)
        assert raised.value.current is current
        assert raised.value.target is target


def test_all_eight_states_are_defined() -> None:
    assert {status.value for status in S} == {
        "requested",
        "planning",
        "blocked_by_policy",
        "applying",
        "ready",
        "failed",
        "destroying",
        "destroyed",
    }
    assert set(TRANSITIONS) == set(S)


def test_destroyed_is_terminal() -> None:
    assert TRANSITIONS[S.DESTROYED] == frozenset()


def test_apply_can_never_be_reached_without_planning() -> None:
    sources = {current for current in S if can_transition(current, S.APPLYING)}
    assert sources == {S.PLANNING}


def test_blocked_plan_cannot_be_applied() -> None:
    assert not can_transition(S.BLOCKED_BY_POLICY, S.APPLYING)
    assert not can_transition(S.BLOCKED_BY_POLICY, S.READY)


def _reachable(start: S) -> set[S]:
    seen: set[S] = set()
    frontier = [start]
    while frontier:
        for target in TRANSITIONS[frontier.pop()]:
            if target not in seen:
                seen.add(target)
                frontier.append(target)
    return seen


@pytest.mark.parametrize("status", [status for status in S if status is not S.DESTROYED])
def test_every_state_can_eventually_be_destroyed(status: S) -> None:
    assert S.DESTROYED in _reachable(status)


def test_failed_can_be_retried_or_destroyed() -> None:
    assert TRANSITIONS[S.FAILED] == {S.PLANNING, S.DESTROYING}
