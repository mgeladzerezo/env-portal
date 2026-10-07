from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PORTAL_", env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./portal.db"

    # "inline" runs operations on a thread pool inside the API process. It exists for
    # local development without Redis; production uses Celery.
    queue_backend: Literal["celery", "inline"] = "celery"
    celery_broker_url: str = "redis://localhost:6379/0"

    terraform_bin: str = "terraform"
    modules_dir: Path = REPO_ROOT / "modules"
    workspace_dir: Path = Path(".portal/workspaces")
    lock_timeout_seconds: int = 30
    command_timeout_seconds: int = 3600

    state_backend: Literal["s3", "local"] = "local"
    state_bucket: str = ""
    state_lock_table: str = ""
    state_region: str = "eu-central-1"
    local_state_dir: Path = Path(".portal/state")

    aws_region: str = "eu-central-1"
    # Template name -> ARN of the role the worker assumes to provision that template.
    provisioner_role_arns: dict[str, str] = {}
    # Template name -> extra module variables supplied by the platform (VPC, subnets, ...).
    template_inputs: dict[str, dict[str, Any]] = {}
    eks_cluster_name: str = ""
    enable_demo_template: bool = False

    policy_checker: Literal["builtin", "conftest"] = "builtin"
    conftest_bin: str = "conftest"
    policy_dir: Path = REPO_ROOT / "policies"

    cost_estimator: Literal["static", "infracost"] = "static"
    infracost_bin: str = "infracost"

    maintenance_interval_seconds: int = 60
    operation_stale_after_seconds: int = 900
    queued_requeue_after_seconds: int = 300
    reaper_max_destroy_attempts: int = 3

    # The portal trusts an authenticating proxy to set this header. dev_user is the
    # fallback identity for local development and must stay unset in production.
    user_header: str = "X-Forwarded-User"
    dev_user: str | None = None
    cors_origins: list[str] = []

    sse_poll_interval_seconds: float = 0.5


@lru_cache
def get_settings() -> Settings:
    return Settings()
