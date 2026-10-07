from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from portal.config import Settings
from portal.container import Container, build_container
from portal.db import Base
from portal.main import create_app
from portal.models import Environment, LogLine, Operation
from portal.services import environments
from portal.terraform.workspace import state_key_for
from tests.fakes import (
    FakeClock,
    FakeCostEstimator,
    FakePolicyChecker,
    FakeTerraformRunner,
    RecordingQueue,
)

USER = "alice@example.com"


@dataclass
class Portal:
    """Test harness: the real container wired with fakes for everything external."""

    container: Container
    runner: FakeTerraformRunner
    policy: FakePolicyChecker
    queue: RecordingQueue
    clock: FakeClock

    def request(
        self,
        template: str = "static-site",
        size: str = "small",
        ttl_hours: int = 4,
        name: str = "demo",
        owner: str = USER,
    ) -> str:
        with self.container.sessions() as session:
            environment, operation = environments.create_environment(
                session,
                catalog=self.container.catalog,
                estimator=self.container.estimator,
                clock=self.clock,
                owner=owner,
                name=name,
                template=template,
                size=size,
                ttl_hours=ttl_hours,
            )
            assert operation is not None
            self.queue.enqueue(operation.id)
            return environment.id

    def ready(self, **kwargs) -> str:
        environment_id = self.request(**kwargs)
        self.run_pending()
        return environment_id

    def retry(self, environment_id: str) -> Operation:
        with self.container.sessions() as session:
            environment = environments.get_environment(session, environment_id)
            operation = environments.request_retry(session, environment, USER, self.clock)
        self.queue.enqueue(operation.id)
        return operation

    def destroy(self, environment_id: str) -> Operation | None:
        with self.container.sessions() as session:
            environment = environments.get_environment(session, environment_id)
            operation = environments.request_destroy(session, environment, USER, self.clock)
        if operation is not None and operation.id not in self.queue.enqueued:
            self.queue.enqueue(operation.id)
        return operation

    def unlock(self, environment_id: str, lock_id: str) -> Operation:
        with self.container.sessions() as session:
            environment = environments.get_environment(session, environment_id)
            operation = environments.request_unlock(session, environment, lock_id, USER, self.clock)
        self.queue.enqueue(operation.id)
        return operation

    def run_pending(self) -> None:
        while self.queue.pending:
            self.container.executor.run(self.queue.pending.pop(0))

    def env(self, environment_id: str) -> Environment:
        with self.container.sessions() as session:
            return environments.get_environment(session, environment_id)

    def operations(self, environment_id: str) -> list[Operation]:
        return self.env(environment_id).operations

    def logs(self, environment_id: str) -> list[LogLine]:
        with self.container.sessions() as session:
            return list(
                session.scalars(
                    select(LogLine)
                    .where(LogLine.environment_id == environment_id)
                    .order_by(LogLine.seq)
                )
            )

    def state_key(self, environment_id: str) -> str:
        return state_key_for(environment_id)


def make_settings(tmp_path: Path, **overrides) -> Settings:
    values = {
        "database_url": f"sqlite:///{(tmp_path / 'portal.db').as_posix()}",
        "workspace_dir": tmp_path / "workspaces",
        "local_state_dir": tmp_path / "state",
        "state_backend": "local",
        "enable_demo_template": True,
        "dev_user": USER,
        "sse_poll_interval_seconds": 0.01,
        **overrides,
    }
    return Settings(_env_file=None, **values)


@pytest.fixture
def portal(tmp_path: Path) -> Iterator[Portal]:
    runner = FakeTerraformRunner()
    policy = FakePolicyChecker()
    queue = RecordingQueue()
    clock = FakeClock()
    container = build_container(
        make_settings(tmp_path),
        runner=runner,
        policy=policy,
        estimator=FakeCostEstimator(),
        queue=queue,
        clock=clock,
    )
    Base.metadata.create_all(container.engine)
    yield Portal(container=container, runner=runner, policy=policy, queue=queue, clock=clock)
    container.engine.dispose()


@pytest.fixture
def client(portal: Portal) -> Iterator[TestClient]:
    with TestClient(create_app(portal.container)) as test_client:
        yield test_client
