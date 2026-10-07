from datetime import UTC, datetime
from pathlib import Path

from portal.catalog import EnvironmentSpec, build_catalog
from portal.config import REPO_ROOT
from portal.terraform.base import parse_lock_error
from portal.terraform.workspace import (
    LocalStateBackend,
    ProviderSettings,
    S3StateBackend,
    WorkspaceManager,
)

# What Terraform 1.9 prints for an S3 backend whose DynamoDB lock is already held.
LOCKED_OUTPUT = """\
╷
│ Error: Error acquiring the state lock
│
│ Error message: operation error DynamoDB: PutItem, https response error
│ StatusCode: 400, ConditionalCheckFailedException: The conditional request failed
│ Lock Info:
│   ID:        9db590f1-b6fe-c5f2-2678-8804f089deba
│   Path:      portal-state/environments/1f0e/terraform.tfstate
│   Operation: OperationTypeApply
│   Who:       worker@portal-worker-7f9c5
│   Version:   1.9.8
│   Created:   2026-01-01 11:58:07.123456 +0000 UTC
│   Info:
│
│ Terraform acquires a state lock to protect the state from being written
│ by multiple users at the same time. Please resolve the issue above and try
│ again. For most commands, you can disable locking with the "-lock=false"
│ flag, but this is not recommended.
╵
""".splitlines()

CATALOG = build_catalog(REPO_ROOT / "modules", include_demo=True)
PROVIDERS = ProviderSettings(
    aws_region="eu-central-1",
    provisioner_role_arns={"vm-database": "arn:aws:iam::123456789012:role/portal-vm-database"},
    template_inputs={"vm-database": {"vpc_id": "vpc-123", "subnet_ids": ["subnet-a", "subnet-b"]}},
    eks_cluster_name="shared-dev",
)
S3 = S3StateBackend(bucket="portal-state", region="eu-central-1", lock_table="portal-locks")


def spec(template: str, environment_id: str = "1f0e7a52-0000-4000-8000-000000000001"):
    return EnvironmentSpec(
        id=environment_id,
        name="checkout-1f0e7a52",
        owner="alice@example.com",
        template=template,
        module_version="v1",
        size="small",
        expires_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def test_lock_error_is_parsed_into_lock_info() -> None:
    lock = parse_lock_error(LOCKED_OUTPUT)

    assert lock is not None
    assert lock.id == "9db590f1-b6fe-c5f2-2678-8804f089deba"
    assert lock.who == "worker@portal-worker-7f9c5"
    assert lock.operation == "OperationTypeApply"
    assert lock.created == "2026-01-01 11:58:07.123456 +0000 UTC"
    assert lock.path == "portal-state/environments/1f0e/terraform.tfstate"


def test_other_errors_are_not_mistaken_for_a_lock() -> None:
    output = ["│ Error: creating EC2 Instance: UnauthorizedOperation", "│   ID: i-123"]

    assert parse_lock_error(output) is None


def test_lock_error_without_details_still_counts_as_locked() -> None:
    lock = parse_lock_error(["Error: Error acquiring the state lock"])

    assert lock is not None
    assert lock.id == "unknown"


def test_s3_backend_uses_one_key_per_environment_and_a_lock_table(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path, S3, CATALOG, PROVIDERS)

    first = manager.render(spec("static-site", "env-a"), "../module", "environments/env-a/x")
    backend = first["terraform"]["backend"]["s3"]

    assert backend == {
        "bucket": "portal-state",
        "key": "environments/env-a/x",
        "region": "eu-central-1",
        "dynamodb_table": "portal-locks",
        "encrypt": True,
    }


def test_prepare_writes_a_root_module_with_a_distinct_state_key(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "ws", LocalStateBackend(tmp_path / "state"), CATALOG)

    first = manager.prepare(spec("local-demo", "env-a"))
    second = manager.prepare(spec("local-demo", "env-b"))

    assert first.state_key == "environments/env-a/terraform.tfstate"
    assert second.state_key == "environments/env-b/terraform.tfstate"
    assert first.path != second.path
    assert (first.path / "main.tf.json").is_file()


def test_root_module_passes_owner_and_expiry_to_the_template_module(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path, S3, CATALOG, PROVIDERS)

    module = manager.render(spec("vm-database"), "../module", "key")["module"]["environment"]

    assert module == {
        "source": "../module",
        "environment_id": "1f0e7a52-0000-4000-8000-000000000001",
        "name": "checkout-1f0e7a52",
        "owner": "alice@example.com",
        "expires_at": "2026-01-02T03:04:05Z",
        "size": "small",
        "vpc_id": "vpc-123",
        "subnet_ids": ["subnet-a", "subnet-b"],
    }


def test_aws_templates_assume_their_own_provisioner_role(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path, S3, CATALOG, PROVIDERS)

    root = manager.render(spec("vm-database"), "../module", "key")

    assert root["provider"]["aws"]["assume_role"]["role_arn"].endswith("role/portal-vm-database")
    assert root["terraform"]["required_providers"]["aws"]["version"] == "~> 5.70"
    assert set(root["output"]) == {
        "instance_id",
        "private_ip",
        "db_endpoint",
        "db_name",
        "db_secret_arn",
    }


def test_kubernetes_template_authenticates_against_the_configured_cluster(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path, S3, CATALOG, PROVIDERS)

    root = manager.render(spec("k8s-namespace"), "../module", "key")

    assert root["data"]["aws_eks_cluster"]["target"] == {"name": "shared-dev"}
    assert "aws_eks_cluster_auth.target.token" in root["provider"]["kubernetes"]["token"]


def test_demo_template_needs_no_provider(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path, LocalStateBackend(tmp_path / "state"), CATALOG)

    root = manager.render(spec("local-demo"), "../module", "key")

    assert "provider" not in root
    assert "required_providers" not in root["terraform"]


def test_module_source_points_at_the_pinned_version_with_a_relative_path(tmp_path: Path) -> None:
    catalog = build_catalog(tmp_path / "modules", include_demo=True)
    manager = WorkspaceManager(tmp_path / "ws", LocalStateBackend(tmp_path / "state"), catalog)

    workspace = manager.prepare(spec("local-demo", "env-a"))

    assert '"source": "../../modules/local-demo/v1"' in (workspace.path / "main.tf.json").read_text(
        encoding="utf-8"
    )
