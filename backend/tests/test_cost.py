import json
import subprocess
from decimal import Decimal
from pathlib import Path

import pytest

from portal.catalog import STATIC_SITE, VM_DATABASE, build_catalog
from portal.config import REPO_ROOT
from portal.cost import infracost as infracost_module
from portal.cost.base import CostEstimationError
from portal.cost.infracost import InfracostEstimator
from portal.cost.static import StaticCostEstimator
from portal.errors import InvalidRequest
from portal.services import environments
from portal.terraform.workspace import LocalStateBackend, WorkspaceManager
from tests.conftest import USER, Portal

REPORT = {
    "totalHourlyCost": "0.0312",
    "projects": [
        {
            "breakdown": {
                "resources": [
                    {"name": "module.environment.aws_instance.vm", "hourlyCost": "0.0120"},
                    {"name": "module.environment.aws_db_instance.this", "hourlyCost": "0.0192"},
                    {"name": "module.environment.aws_cloudwatch_log_group.x", "hourlyCost": None},
                ]
            }
        }
    ],
}


def infracost(tmp_path: Path) -> InfracostEstimator:
    catalog = build_catalog(REPO_ROOT / "modules", include_demo=False)
    workspaces = WorkspaceManager(tmp_path / "ws", LocalStateBackend(tmp_path / "state"), catalog)
    return InfracostEstimator("infracost", catalog, workspaces)


def fake_infracost(monkeypatch: pytest.MonkeyPatch, returncode: int, stdout: str) -> list[dict]:
    rendered: list[dict] = []

    def run(command, **kwargs):
        directory = Path(command[command.index("--path") + 1])
        rendered.append(json.loads((directory / "main.tf.json").read_text(encoding="utf-8")))
        return subprocess.CompletedProcess(command, returncode, stdout, "boom")

    monkeypatch.setattr(infracost_module.subprocess, "run", run)
    return rendered


def test_static_estimate_uses_the_catalog_price() -> None:
    estimate = StaticCostEstimator().estimate(VM_DATABASE, "medium")

    assert estimate.hourly_usd == Decimal("0.0640")
    assert estimate.total_for(24) == Decimal("1.54")
    assert estimate.source == "static"


def test_static_estimate_rejects_unknown_sizes() -> None:
    with pytest.raises(InvalidRequest):
        StaticCostEstimator().estimate(STATIC_SITE, "huge")


def test_infracost_report_is_parsed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rendered = fake_infracost(monkeypatch, 0, json.dumps(REPORT))

    estimate = infracost(tmp_path).estimate(VM_DATABASE, "small")

    assert estimate.hourly_usd == Decimal("0.0312")
    assert estimate.source == "infracost"
    assert [item.name for item in estimate.items] == [
        "module.environment.aws_instance.vm",
        "module.environment.aws_db_instance.this",
    ]
    (root,) = rendered
    assert root["module"]["environment"]["size"] == "small"
    assert "backend" not in root["terraform"]


@pytest.mark.parametrize(("returncode", "stdout"), [(1, ""), (0, "<html>")])
def test_infracost_failures_raise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, stdout: str
) -> None:
    fake_infracost(monkeypatch, returncode, stdout)

    with pytest.raises(CostEstimationError):
        infracost(tmp_path).estimate(VM_DATABASE, "small")


def test_environment_is_still_created_when_pricing_is_unavailable(portal: Portal) -> None:
    class BrokenEstimator:
        def estimate(self, template, size):
            raise CostEstimationError("pricing API unreachable")

    with portal.container.sessions() as session:
        environment, operation = environments.create_environment(
            session,
            catalog=portal.container.catalog,
            estimator=BrokenEstimator(),
            clock=portal.clock,
            owner=USER,
            name="demo",
            template="static-site",
            size="small",
            ttl_hours=2,
        )

    assert operation is not None
    assert environment.hourly_cost_usd is None
