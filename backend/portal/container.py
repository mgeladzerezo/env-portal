from dataclasses import dataclass
from datetime import timedelta
from functools import lru_cache

from sqlalchemy import Engine

from portal.catalog import Catalog, build_catalog
from portal.clock import Clock, SystemClock
from portal.config import Settings, get_settings
from portal.cost.base import CostEstimator
from portal.cost.infracost import InfracostEstimator
from portal.cost.static import StaticCostEstimator
from portal.db import SessionFactory, make_engine, make_session_factory
from portal.policy.base import PolicyChecker
from portal.policy.builtin import BuiltinPolicyChecker
from portal.policy.conftest import ConftestPolicyChecker
from portal.queue import CeleryTaskQueue, InlineTaskQueue, TaskQueue
from portal.services.executor import OperationExecutor
from portal.services.maintenance import (
    Reaper,
    ReapReport,
    recover_stale_operations,
    requeue_stuck_operations,
)
from portal.terraform.base import TerraformRunner
from portal.terraform.subprocess_runner import SubprocessTerraformRunner
from portal.terraform.workspace import (
    LocalStateBackend,
    ProviderSettings,
    S3StateBackend,
    StateBackend,
    WorkspaceManager,
)


@dataclass
class Container:
    """Everything the API and the workers share, wired once per process."""

    settings: Settings
    engine: Engine
    sessions: SessionFactory
    clock: Clock
    catalog: Catalog
    estimator: CostEstimator
    executor: OperationExecutor
    queue: TaskQueue
    reaper: Reaper

    def run_maintenance(self) -> ReapReport:
        recover_stale_operations(
            self.sessions,
            self.clock,
            timedelta(seconds=self.settings.operation_stale_after_seconds),
        )
        requeue_stuck_operations(
            self.sessions,
            self.queue,
            self.clock,
            timedelta(seconds=self.settings.queued_requeue_after_seconds),
        )
        return self.reaper.run_once()


def _state_backend(settings: Settings) -> StateBackend:
    if settings.state_backend == "s3":
        if not settings.state_bucket or not settings.state_lock_table:
            raise ValueError(
                "the s3 state backend needs PORTAL_STATE_BUCKET and PORTAL_STATE_LOCK_TABLE"
            )
        return S3StateBackend(
            bucket=settings.state_bucket,
            region=settings.state_region,
            lock_table=settings.state_lock_table,
        )
    return LocalStateBackend(settings.local_state_dir)


def build_container(
    settings: Settings,
    *,
    runner: TerraformRunner | None = None,
    policy: PolicyChecker | None = None,
    estimator: CostEstimator | None = None,
    queue: TaskQueue | None = None,
    clock: Clock | None = None,
    catalog: Catalog | None = None,
) -> Container:
    """Build the object graph from settings. Tests pass fakes for the external pieces."""
    engine = make_engine(settings.database_url)
    sessions = make_session_factory(engine)
    clock = clock or SystemClock()
    catalog = catalog or build_catalog(
        settings.modules_dir, include_demo=settings.enable_demo_template
    )
    workspaces = WorkspaceManager(
        settings.workspace_dir,
        _state_backend(settings),
        catalog,
        ProviderSettings(
            aws_region=settings.aws_region,
            provisioner_role_arns=settings.provisioner_role_arns,
            template_inputs=settings.template_inputs,
            eks_cluster_name=settings.eks_cluster_name,
        ),
    )
    runner = runner or SubprocessTerraformRunner(
        settings.terraform_bin,
        lock_timeout_seconds=settings.lock_timeout_seconds,
        command_timeout_seconds=settings.command_timeout_seconds,
    )
    if policy is None:
        policy = (
            ConftestPolicyChecker(settings.conftest_bin, settings.policy_dir)
            if settings.policy_checker == "conftest"
            else BuiltinPolicyChecker()
        )
    if estimator is None:
        estimator = (
            InfracostEstimator(settings.infracost_bin, catalog, workspaces)
            if settings.cost_estimator == "infracost"
            else StaticCostEstimator()
        )
    executor = OperationExecutor(sessions, runner, policy, workspaces, clock)
    if queue is None:
        queue = (
            InlineTaskQueue(executor.run)
            if settings.queue_backend == "inline"
            else CeleryTaskQueue()
        )
    reaper = Reaper(sessions, queue, clock, settings.reaper_max_destroy_attempts)
    return Container(
        settings=settings,
        engine=engine,
        sessions=sessions,
        clock=clock,
        catalog=catalog,
        estimator=estimator,
        executor=executor,
        queue=queue,
        reaper=reaper,
    )


@lru_cache
def get_container() -> Container:
    return build_container(get_settings())
