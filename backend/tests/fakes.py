import threading
from collections import defaultdict
from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from portal.catalog import TemplateSpec
from portal.cost.base import CostEstimate, CostItem
from portal.policy.base import PolicyResult, Violation
from portal.terraform.base import (
    LockInfo,
    LogSink,
    PlanResult,
    StateLockedError,
    TerraformCommandError,
    Workspace,
)


class FakeClock:
    def __init__(self, start: datetime | None = None) -> None:
        self._now = start or datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> None:
        self._now += timedelta(**delta)


class RecordingQueue:
    """Collects enqueued operation ids so a test decides when each one runs."""

    def __init__(self) -> None:
        self.enqueued: list[int] = []
        self.pending: list[int] = []
        self.fail_next = False

    def enqueue(self, operation_id: int) -> None:
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("broker unavailable")
        self.enqueued.append(operation_id)
        self.pending.append(operation_id)


class FakePolicyChecker:
    def __init__(self) -> None:
        self.violations: list[Violation] = []
        self.error: Exception | None = None
        self.plans: list[dict[str, Any]] = []

    def check(self, plan: dict[str, Any]) -> PolicyResult:
        self.plans.append(plan)
        if self.error is not None:
            raise self.error
        return PolicyResult(tuple(self.violations))


class FakeCostEstimator:
    def __init__(self, hourly: str = "0.2500") -> None:
        self.hourly = Decimal(hourly)

    def estimate(self, template: TemplateSpec, size: str) -> CostEstimate:
        template.size(size)
        return CostEstimate(
            hourly_usd=self.hourly,
            items=(CostItem(name=f"{template.name}/{size}", hourly_usd=self.hourly),),
            source="fake",
        )


class FakeTerraformRunner:
    """In-memory stand-in for Terraform that keeps one resource list per state key.

    It behaves like the real thing where the portal depends on it: apply only creates
    what the last plan found missing, a failed apply keeps what it already created, and
    a locked state refuses every command that needs the lock.
    """

    RESOURCES = ("network", "compute", "database")

    def __init__(self) -> None:
        self.state: dict[str, list[str]] = defaultdict(list)
        self.calls: list[tuple[str, str]] = []
        self.locks: dict[str, LockInfo] = {}
        self.failures: dict[str, list[Exception]] = defaultdict(list)
        self.hooks: dict[str, Callable[[Workspace], None]] = {}
        # Create this many resources during the next apply, then fail.
        self.apply_fails_after: int | None = None
        self.overlapping_commands = 0
        self._planned: dict[str, list[str]] = {}
        self._busy: set[str] = set()
        self._guard = threading.Lock()

    def commands(self, state_key: str | None = None) -> list[str]:
        return [command for command, key in self.calls if state_key in (None, key)]

    def init(self, workspace: Workspace, log: LogSink) -> None:
        with self._command("init", workspace, needs_lock=False):
            log("Initializing the backend...")
            log("Terraform has been successfully initialized!")

    def plan(self, workspace: Workspace, log: LogSink) -> PlanResult:
        with self._command("plan", workspace):
            existing = self.state[workspace.state_key]
            missing = [name for name in self.RESOURCES if name not in existing]
            self._planned[workspace.state_key] = missing
            for name in missing:
                log(f"  # fake.{name} will be created")
            log(f"Plan: {len(missing)} to add, 0 to change, 0 to destroy.")
            return PlanResult(has_changes=bool(missing), plan=self._plan_json(missing))

    def apply(self, workspace: Workspace, log: LogSink) -> None:
        with self._command("apply", workspace):
            created = 0
            for name in self._planned.pop(workspace.state_key, []):
                if self.apply_fails_after is not None and created == self.apply_fails_after:
                    self.apply_fails_after = None
                    log(f"Error: creating fake.{name}: quota exceeded")
                    raise TerraformCommandError("apply", 1, [f"Error: creating fake.{name}"])
                self.state[workspace.state_key].append(name)
                created += 1
                log(f"fake.{name}: Creation complete")
                self._hook("apply_step", workspace)
            log(f"Apply complete! Resources: {created} added, 0 changed, 0 destroyed.")

    def destroy(self, workspace: Workspace, log: LogSink) -> None:
        with self._command("destroy", workspace):
            resources = self.state[workspace.state_key]
            for name in reversed(resources):
                log(f"fake.{name}: Destruction complete")
            log(f"Destroy complete! Resources: {len(resources)} destroyed.")
            resources.clear()

    def output(self, workspace: Workspace) -> dict[str, Any]:
        self.calls.append(("output", workspace.state_key))
        return {"resources": list(self.state[workspace.state_key])}

    def force_unlock(self, workspace: Workspace, lock_id: str, log: LogSink) -> None:
        with self._command("force-unlock", workspace, needs_lock=False):
            held = self.locks.get(workspace.state_key)
            if held is None or held.id != lock_id:
                raise TerraformCommandError("force-unlock", 1, ["Error: lock id does not match"])
            del self.locks[workspace.state_key]
            log("Terraform state has been successfully unlocked!")

    @contextmanager
    def _command(self, command: str, workspace: Workspace, needs_lock: bool = True):
        key = workspace.state_key
        with self._guard:
            self.calls.append((command, key))
            if key in self._busy:
                self.overlapping_commands += 1
            self._busy.add(key)
        try:
            if needs_lock and key in self.locks:
                raise StateLockedError(command, self.locks[key])
            self._hook(command, workspace)
            if self.failures[command]:
                raise self.failures[command].pop(0)
            yield
        finally:
            with self._guard:
                self._busy.discard(key)

    def _hook(self, name: str, workspace: Workspace) -> None:
        if name in self.hooks:
            self.hooks[name](workspace)

    def _plan_json(self, missing: list[str]) -> dict[str, Any]:
        return {
            "format_version": "1.2",
            "resource_changes": [
                {
                    "address": f"fake.{name}",
                    "mode": "managed",
                    "type": "fake",
                    "change": {"actions": ["create"], "after": {}},
                }
                for name in missing
            ],
        }
