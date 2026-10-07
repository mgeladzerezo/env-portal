import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from portal.errors import PortalError

LogSink = Callable[[str], None]


@dataclass(frozen=True)
class Workspace:
    path: Path
    state_key: str


@dataclass(frozen=True)
class PlanResult:
    has_changes: bool
    # Output of `terraform show -json` for the saved plan file.
    plan: dict[str, Any]


@dataclass(frozen=True)
class LockInfo:
    id: str
    who: str = ""
    operation: str = ""
    created: str = ""
    path: str = ""

    def as_dict(self) -> dict[str, str]:
        return asdict(self)


class TerraformError(PortalError):
    pass


class TerraformCommandError(TerraformError):
    def __init__(self, command: str, exit_code: int, tail: list[str]) -> None:
        detail = "\n".join(tail).strip()
        super().__init__(f"terraform {command} exited with code {exit_code}\n{detail}".strip())
        self.command = command
        self.exit_code = exit_code


class TerraformTimeout(TerraformError):
    def __init__(self, command: str, seconds: int) -> None:
        super().__init__(f"terraform {command} was killed after {seconds}s")
        self.command = command


class StateLockedError(TerraformError):
    def __init__(self, command: str, lock: LockInfo) -> None:
        holder = f" by {lock.who}" if lock.who else ""
        since = f" since {lock.created}" if lock.created else ""
        super().__init__(
            f"terraform {command} could not acquire the state lock: "
            f"lock {lock.id} is held{holder}{since}"
        )
        self.command = command
        self.lock = lock


class TerraformRunner(Protocol):
    def init(self, workspace: Workspace, log: LogSink) -> None: ...

    def plan(self, workspace: Workspace, log: LogSink) -> PlanResult: ...

    def apply(self, workspace: Workspace, log: LogSink) -> None:
        """Apply exactly the plan produced by the preceding plan() call."""

    def destroy(self, workspace: Workspace, log: LogSink) -> None: ...

    def output(self, workspace: Workspace) -> dict[str, Any]: ...

    def force_unlock(self, workspace: Workspace, lock_id: str, log: LogSink) -> None: ...


LOCK_ERROR_MARKER = "Error acquiring the state lock"
_LOCK_FIELD = re.compile(r"^\W*(ID|Path|Operation|Who|Created):\s*(.*?)\s*$")


def parse_lock_error(lines: list[str]) -> LockInfo | None:
    """Extract the "Lock Info" block Terraform prints when the state is already locked."""
    if not any(LOCK_ERROR_MARKER in line for line in lines):
        return None
    fields: dict[str, str] = {}
    for line in lines:
        match = _LOCK_FIELD.match(line)
        if match:
            fields.setdefault(match.group(1).lower(), match.group(2))
    return LockInfo(
        id=fields.get("id", "unknown"),
        who=fields.get("who", ""),
        operation=fields.get("operation", ""),
        created=fields.get("created", ""),
        path=fields.get("path", ""),
    )
