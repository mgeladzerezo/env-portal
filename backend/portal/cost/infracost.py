import json
import subprocess
import tempfile
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from portal.catalog import Catalog, EnvironmentSpec, TemplateSpec
from portal.cost.base import CostEstimate, CostEstimationError, CostItem
from portal.terraform.workspace import WorkspaceManager


class InfracostEstimator:
    """Prices a template by rendering its root module and running `infracost breakdown`.

    Infracost parses the HCL itself, so this needs neither a plan nor cloud credentials.
    """

    def __init__(
        self,
        binary: str,
        catalog: Catalog,
        workspaces: WorkspaceManager,
        timeout_seconds: int = 120,
    ) -> None:
        self._binary = binary
        self._catalog = catalog
        self._workspaces = workspaces
        self._timeout = timeout_seconds

    def estimate(self, template: TemplateSpec, size: str) -> CostEstimate:
        template.size(size)
        spec = EnvironmentSpec(
            id="00000000-0000-0000-0000-000000000000",
            name="estimate",
            owner="estimate",
            template=template.name,
            module_version=template.module_version,
            size=size,
            expires_at=datetime.now(UTC),
        )
        module = self._catalog.module_path(template.name, template.module_version).resolve()
        with tempfile.TemporaryDirectory(prefix="portal-estimate-") as directory:
            root = self._workspaces.render(spec, module.as_posix(), "estimate.tfstate")
            # The estimate never touches state, so the backend block would only make
            # infracost try to initialise it.
            root["terraform"].pop("backend", None)
            Path(directory, "main.tf.json").write_text(json.dumps(root), encoding="utf-8")
            report = self._breakdown(directory)
        return self._parse(report)

    def _breakdown(self, directory: str) -> dict[str, Any]:
        command = [self._binary, "breakdown", "--path", directory, "--format", "json"]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=self._timeout, check=False
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise CostEstimationError(f"could not run infracost: {exc}") from exc
        if result.returncode != 0:
            raise CostEstimationError(
                f"infracost exited with code {result.returncode}: {result.stderr.strip()}"
            )
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise CostEstimationError("infracost returned output that is not JSON") from exc

    def _parse(self, report: dict[str, Any]) -> CostEstimate:
        try:
            items = tuple(
                CostItem(name=resource["name"], hourly_usd=Decimal(resource["hourlyCost"]))
                for project in report.get("projects", [])
                for resource in (project.get("breakdown") or {}).get("resources", [])
                # Usage-based resources (requests, data transfer) have no hourly cost.
                if resource.get("hourlyCost") is not None
            )
            hourly = Decimal(report.get("totalHourlyCost") or "0")
        except (KeyError, InvalidOperation) as exc:
            raise CostEstimationError("unexpected infracost report format") from exc
        return CostEstimate(hourly_usd=hourly, items=items, source="infracost")
