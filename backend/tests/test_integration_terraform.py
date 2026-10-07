"""Drives the real Terraform binary against modules/local-demo (terraform_data, local state).

Opt-in: set TERRAFORM_BIN or have `terraform` on PATH. Needs no network or credentials.
"""

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from portal.catalog import EnvironmentSpec, build_catalog
from portal.config import REPO_ROOT
from portal.container import build_container
from portal.db import Base
from portal.policy.builtin import BuiltinPolicyChecker
from portal.state_machine import EnvironmentStatus
from portal.terraform.base import TerraformCommandError
from portal.terraform.subprocess_runner import SubprocessTerraformRunner
from portal.terraform.workspace import LocalStateBackend, WorkspaceManager
from tests.conftest import Portal, make_settings
from tests.fakes import FakeClock, FakeCostEstimator, FakePolicyChecker, RecordingQueue

TERRAFORM = os.environ.get("TERRAFORM_BIN") or shutil.which("terraform")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        TERRAFORM is None or not Path(TERRAFORM).is_file(),
        reason="terraform binary not found (set TERRAFORM_BIN)",
    ),
]

S = EnvironmentStatus


@pytest.fixture
def runner() -> SubprocessTerraformRunner:
    return SubprocessTerraformRunner(TERRAFORM, lock_timeout_seconds=5, command_timeout_seconds=120)


def spec(environment_id: str, size: str = "medium") -> EnvironmentSpec:
    return EnvironmentSpec(
        id=environment_id,
        name=f"demo-{environment_id}",
        owner="alice@example.com",
        template="local-demo",
        module_version="v1",
        size=size,
        expires_at=datetime(2026, 1, 2, tzinfo=UTC),
    )


def test_runner_applies_reads_outputs_and_destroys(
    runner: SubprocessTerraformRunner, tmp_path: Path
) -> None:
    catalog = build_catalog(REPO_ROOT / "modules", include_demo=True)
    manager = WorkspaceManager(tmp_path / "ws", LocalStateBackend(tmp_path / "state"), catalog)
    workspace = manager.prepare(spec("it-1"))
    state_file = tmp_path / "state" / "environments" / "it-1" / "terraform.tfstate"
    output: list[str] = []

    runner.init(workspace, output.append)
    plan = runner.plan(workspace, output.append)

    assert plan.has_changes
    created = [
        change["address"]
        for change in plan.plan["resource_changes"]
        if change["change"]["actions"] == ["create"]
    ]
    assert created == [
        "module.environment.terraform_data.replica[0]",
        "module.environment.terraform_data.replica[1]",
    ]
    assert BuiltinPolicyChecker().check(plan.plan).passed

    runner.apply(workspace, output.append)

    assert runner.output(workspace) == {
        "replicas": ["demo-it-1-0", "demo-it-1-1"],
        "summary": "demo-it-1: 2 placeholder resource(s) for alice@example.com",
    }
    assert any("Apply complete! Resources: 2 added" in line for line in output)
    assert len(json.loads(state_file.read_text(encoding="utf-8"))["resources"]) == 1

    # Running the same thing again is a no-op: nothing is created twice.
    assert not runner.plan(workspace, output.append).has_changes

    runner.destroy(workspace, output.append)

    assert any("Destroy complete! Resources: 2 destroyed" in line for line in output)
    assert json.loads(state_file.read_text(encoding="utf-8"))["resources"] == []
    assert runner.output(workspace) == {}


def test_runner_reports_a_failing_command_with_its_output(
    runner: SubprocessTerraformRunner, tmp_path: Path
) -> None:
    catalog = build_catalog(tmp_path / "no-modules-here", include_demo=True)
    manager = WorkspaceManager(tmp_path / "ws", LocalStateBackend(tmp_path / "state"), catalog)
    workspace = manager.prepare(spec("it-2"))
    output: list[str] = []

    with pytest.raises(TerraformCommandError) as raised:
        runner.init(workspace, output.append)

    assert raised.value.command == "init"
    assert raised.value.exit_code == 1
    assert any("Error" in line for line in output)


@pytest.fixture
def real_portal(runner: SubprocessTerraformRunner, tmp_path: Path) -> Portal:
    queue, clock, policy = RecordingQueue(), FakeClock(), FakePolicyChecker()
    container = build_container(
        make_settings(tmp_path, modules_dir=REPO_ROOT / "modules"),
        runner=runner,
        policy=BuiltinPolicyChecker(),
        estimator=FakeCostEstimator(),
        queue=queue,
        clock=clock,
    )
    Base.metadata.create_all(container.engine)
    yield Portal(container=container, runner=None, policy=policy, queue=queue, clock=clock)
    container.engine.dispose()


def test_portal_provisions_and_reaps_an_environment_with_real_terraform(
    real_portal: Portal, tmp_path: Path
) -> None:
    environment_id = real_portal.ready(template="local-demo", size="large", ttl_hours=1)

    environment = real_portal.env(environment_id)
    assert environment.status is S.READY, environment.error
    assert len(environment.outputs["replicas"]) == 4
    messages = [line.message for line in real_portal.logs(environment_id)]
    assert any("Apply complete! Resources: 4 added" in message for message in messages)
    assert (tmp_path / "workspaces" / environment_id / "main.tf.json").is_file()

    real_portal.clock.advance(hours=2)
    assert real_portal.container.reaper.run_once().destroyed == [environment_id]
    real_portal.run_pending()

    destroyed = real_portal.env(environment_id)
    assert destroyed.status is S.DESTROYED, destroyed.error
    assert destroyed.outputs == {}
    assert not (tmp_path / "workspaces" / environment_id).exists()
    state_file = tmp_path / "state" / "environments" / environment_id / "terraform.tfstate"
    assert json.loads(state_file.read_text(encoding="utf-8"))["resources"] == []
