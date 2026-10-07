from enum import StrEnum

from portal.errors import PortalError


class EnvironmentStatus(StrEnum):
    REQUESTED = "requested"
    PLANNING = "planning"
    BLOCKED_BY_POLICY = "blocked_by_policy"
    APPLYING = "applying"
    READY = "ready"
    FAILED = "failed"
    DESTROYING = "destroying"
    DESTROYED = "destroyed"


S = EnvironmentStatus

# FAILED keeps whatever Terraform managed to create in the state file, so the only ways
# out are planning again (retry) or destroying. DESTROYED is terminal.
TRANSITIONS: dict[EnvironmentStatus, frozenset[EnvironmentStatus]] = {
    S.REQUESTED: frozenset({S.PLANNING, S.DESTROYING}),
    S.PLANNING: frozenset({S.APPLYING, S.BLOCKED_BY_POLICY, S.FAILED}),
    S.BLOCKED_BY_POLICY: frozenset({S.PLANNING, S.DESTROYING}),
    S.APPLYING: frozenset({S.READY, S.FAILED}),
    S.READY: frozenset({S.DESTROYING}),
    S.FAILED: frozenset({S.PLANNING, S.DESTROYING}),
    S.DESTROYING: frozenset({S.DESTROYED, S.FAILED}),
    S.DESTROYED: frozenset(),
}


class IllegalTransition(PortalError):
    def __init__(self, current: EnvironmentStatus, target: EnvironmentStatus) -> None:
        super().__init__(f"cannot move environment from {current.value} to {target.value}")
        self.current = current
        self.target = target


def can_transition(current: EnvironmentStatus, target: EnvironmentStatus) -> bool:
    return target in TRANSITIONS[current]


def assert_transition(current: EnvironmentStatus, target: EnvironmentStatus) -> None:
    if not can_transition(current, target):
        raise IllegalTransition(current, target)
