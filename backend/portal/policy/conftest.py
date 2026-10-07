import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from portal.policy.base import PolicyCheckError, PolicyResult, Violation

# conftest exits 0 when every policy passes and 1 when at least one fails.
_PASS, _FAIL = 0, 1


class ConftestPolicyChecker:
    def __init__(self, binary: str, policy_dir: Path, timeout_seconds: int = 120) -> None:
        self._binary = binary
        self._policy_dir = policy_dir
        self._timeout = timeout_seconds

    def check(self, plan: dict[str, Any]) -> PolicyResult:
        with tempfile.TemporaryDirectory(prefix="portal-policy-") as directory:
            plan_file = Path(directory) / "plan.json"
            plan_file.write_text(json.dumps(plan), encoding="utf-8")
            result = self._run(plan_file)
        if result.returncode not in (_PASS, _FAIL):
            raise PolicyCheckError(
                f"conftest exited with code {result.returncode}: {result.stderr.strip()}"
            )
        violations = self._parse(result.stdout)
        if result.returncode == _FAIL and not violations:
            raise PolicyCheckError("conftest reported a failure but returned no violations")
        return PolicyResult(violations)

    def _run(self, plan_file: Path) -> subprocess.CompletedProcess[str]:
        command = [
            self._binary,
            "test",
            "--output=json",
            "--no-color",
            f"--policy={self._policy_dir}",
            str(plan_file),
        ]
        try:
            return subprocess.run(
                command, capture_output=True, text=True, timeout=self._timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PolicyCheckError(f"could not run conftest: {exc}") from exc

    def _parse(self, stdout: str) -> tuple[Violation, ...]:
        try:
            files = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise PolicyCheckError("conftest returned output that is not JSON") from exc
        violations = []
        for file_result in files:
            for failure in file_result.get("failures") or []:
                metadata = failure.get("metadata") or {}
                violations.append(
                    Violation(
                        rule=str(metadata.get("rule", "conftest")),
                        resource=str(metadata.get("resource", "")),
                        message=str(failure.get("msg", "")),
                    )
                )
        return tuple(violations)
