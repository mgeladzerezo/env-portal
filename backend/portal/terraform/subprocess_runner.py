import json
import os
import subprocess
import threading
from collections import deque
from typing import Any

from portal.terraform.base import (
    LogSink,
    PlanResult,
    StateLockedError,
    TerraformCommandError,
    TerraformTimeout,
    Workspace,
    parse_lock_error,
)

PLAN_FILE = "tfplan"
_TAIL_LINES = 40
# `plan -detailed-exitcode` returns 2 when the plan succeeded and contains changes.
_PLAN_HAS_CHANGES = 2


class SubprocessTerraformRunner:
    def __init__(
        self,
        binary: str,
        *,
        lock_timeout_seconds: int = 30,
        command_timeout_seconds: int = 3600,
    ) -> None:
        self._binary = binary
        self._lock_timeout = f"-lock-timeout={lock_timeout_seconds}s"
        self._command_timeout = command_timeout_seconds
        # Terraform refuses to start when the plugin cache directory is missing, which
        # is the normal case on a freshly mounted volume.
        if plugin_cache := os.environ.get("TF_PLUGIN_CACHE_DIR"):
            os.makedirs(plugin_cache, exist_ok=True)

    def init(self, workspace: Workspace, log: LogSink) -> None:
        self._run(workspace, ["init", "-input=false", "-no-color"], log)

    def plan(self, workspace: Workspace, log: LogSink) -> PlanResult:
        exit_code = self._run(
            workspace,
            [
                "plan",
                "-input=false",
                "-no-color",
                "-detailed-exitcode",
                self._lock_timeout,
                f"-out={PLAN_FILE}",
            ],
            log,
            ok_codes=(0, _PLAN_HAS_CHANGES),
        )
        plan = json.loads(self._capture(workspace, ["show", "-json", PLAN_FILE]))
        return PlanResult(has_changes=exit_code == _PLAN_HAS_CHANGES, plan=plan)

    def apply(self, workspace: Workspace, log: LogSink) -> None:
        # Applying the saved plan file guarantees that what gets created is what the
        # policy check looked at. Terraform refuses the file if the state moved since.
        self._run(
            workspace,
            ["apply", "-input=false", "-no-color", self._lock_timeout, PLAN_FILE],
            log,
        )

    def destroy(self, workspace: Workspace, log: LogSink) -> None:
        self._run(
            workspace,
            ["destroy", "-auto-approve", "-input=false", "-no-color", self._lock_timeout],
            log,
        )

    def output(self, workspace: Workspace) -> dict[str, Any]:
        raw = json.loads(self._capture(workspace, ["output", "-json"]))
        return {name: entry["value"] for name, entry in raw.items()}

    def force_unlock(self, workspace: Workspace, lock_id: str, log: LogSink) -> None:
        self._run(workspace, ["force-unlock", "-force", lock_id], log)

    def _environment(self) -> dict[str, str]:
        return {**os.environ, "TF_IN_AUTOMATION": "1", "TF_INPUT": "0"}

    def _run(
        self,
        workspace: Workspace,
        args: list[str],
        log: LogSink,
        ok_codes: tuple[int, ...] = (0,),
    ) -> int:
        command = args[0]
        # stderr is merged into stdout so one reader sees the lines in the order
        # Terraform wrote them.
        process = subprocess.Popen(
            [self._binary, *args],
            cwd=workspace.path,
            env=self._environment(),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        timed_out = threading.Event()

        def kill() -> None:
            timed_out.set()
            process.kill()

        watchdog = threading.Timer(self._command_timeout, kill)
        watchdog.start()
        tail: deque[str] = deque(maxlen=_TAIL_LINES)
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                tail.append(line)
                log(line)
            exit_code = process.wait()
        finally:
            watchdog.cancel()
            if process.poll() is None:
                process.kill()
                process.wait()

        if timed_out.is_set():
            raise TerraformTimeout(command, self._command_timeout)
        if exit_code in ok_codes:
            return exit_code
        lock = parse_lock_error(list(tail))
        if lock is not None:
            raise StateLockedError(command, lock)
        raise TerraformCommandError(command, exit_code, list(tail))

    def _capture(self, workspace: Workspace, args: list[str]) -> str:
        result = subprocess.run(
            [self._binary, *args],
            cwd=workspace.path,
            env=self._environment(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._command_timeout,
            check=False,
        )
        if result.returncode != 0:
            raise TerraformCommandError(args[0], result.returncode, result.stderr.splitlines())
        return result.stdout
