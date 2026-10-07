from dataclasses import asdict, dataclass
from typing import Any, Protocol

from portal.errors import PortalError


@dataclass(frozen=True)
class Violation:
    rule: str
    resource: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


@dataclass(frozen=True)
class PolicyResult:
    violations: tuple[Violation, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.violations


class PolicyCheckError(PortalError):
    """The checker itself failed. Callers must treat this as "not approved"."""


class PolicyChecker(Protocol):
    def check(self, plan: dict[str, Any]) -> PolicyResult: ...
