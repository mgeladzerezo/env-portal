from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from portal.errors import InvalidRequest

MIN_TTL_HOURS = 1


@dataclass(frozen=True)
class SizeSpec:
    name: str
    description: str
    # Rough on-demand price used by the static estimator when Infracost is not configured.
    hourly_usd: Decimal


@dataclass(frozen=True)
class TemplateSpec:
    name: str
    title: str
    description: str
    # New environments get this version. Existing ones keep the version they were
    # created with, so publishing v2 of a module never changes a running environment.
    module_version: str
    sizes: tuple[SizeSpec, ...]
    outputs: tuple[str, ...]
    providers: frozenset[str] = frozenset()
    default_ttl_hours: int = 8
    max_ttl_hours: int = 168

    def size(self, name: str) -> SizeSpec:
        for size in self.sizes:
            if size.name == name:
                return size
        raise InvalidRequest(f"template {self.name} has no size {name!r}")


@dataclass(frozen=True)
class EnvironmentSpec:
    """The inputs Terraform needs for one environment, detached from the ORM."""

    id: str
    name: str
    owner: str
    template: str
    module_version: str
    size: str
    expires_at: datetime


def _sizes(*rows: tuple[str, str, str]) -> tuple[SizeSpec, ...]:
    return tuple(SizeSpec(name, description, Decimal(price)) for name, description, price in rows)


VM_DATABASE = TemplateSpec(
    name="vm-database",
    title="VM + database",
    description="One EC2 instance and a PostgreSQL RDS instance in private subnets.",
    module_version="v1",
    sizes=_sizes(
        ("small", "t3.micro + db.t4g.micro, 20 GB", "0.0330"),
        ("medium", "t3.small + db.t4g.small, 50 GB", "0.0640"),
        ("large", "t3.medium + db.t4g.medium, 100 GB", "0.1260"),
    ),
    outputs=("instance_id", "private_ip", "db_endpoint", "db_name", "db_secret_arn"),
    providers=frozenset({"aws"}),
)

STATIC_SITE = TemplateSpec(
    name="static-site",
    title="Static site",
    description="Private S3 bucket served through CloudFront.",
    module_version="v1",
    sizes=_sizes(
        ("small", "Europe and North America edge locations", "0.0050"),
        ("medium", "Adds Asia, Middle East and Africa", "0.0100"),
        ("large", "All edge locations", "0.0200"),
    ),
    outputs=("bucket_name", "distribution_id", "site_url"),
    providers=frozenset({"aws"}),
)

K8S_NAMESPACE = TemplateSpec(
    name="k8s-namespace",
    title="Kubernetes namespace",
    description="Namespace on the shared cluster with a quota, limits and default-deny network.",
    module_version="v1",
    sizes=_sizes(
        ("small", "2 vCPU / 4 GiB quota", "0.0500"),
        ("medium", "4 vCPU / 8 GiB quota", "0.1000"),
        ("large", "8 vCPU / 16 GiB quota", "0.2000"),
    ),
    outputs=("namespace", "cpu_quota", "memory_quota"),
    providers=frozenset({"aws", "kubernetes"}),
)

# Uses only the built-in terraform_data resource, so the full request -> apply -> destroy
# flow can be exercised without cloud credentials.
LOCAL_DEMO = TemplateSpec(
    name="local-demo",
    title="Local demo (no cloud resources)",
    description="Creates terraform_data resources only. For trying the portal locally.",
    module_version="v1",
    sizes=_sizes(
        ("small", "1 placeholder resource", "0"),
        ("medium", "2 placeholder resources", "0"),
        ("large", "4 placeholder resources", "0"),
    ),
    outputs=("summary", "replicas"),
)


class Catalog:
    def __init__(self, templates: tuple[TemplateSpec, ...], modules_dir: Path) -> None:
        self._templates = {template.name: template for template in templates}
        self._modules_dir = modules_dir

    def all(self) -> list[TemplateSpec]:
        return list(self._templates.values())

    def get(self, name: str) -> TemplateSpec:
        try:
            return self._templates[name]
        except KeyError:
            raise InvalidRequest(f"unknown template {name!r}") from None

    def module_path(self, template: str, version: str) -> Path:
        return self._modules_dir / template / version

    def validate_request(self, template: str, size: str, ttl_hours: int) -> TemplateSpec:
        spec = self.get(template)
        spec.size(size)
        if not MIN_TTL_HOURS <= ttl_hours <= spec.max_ttl_hours:
            raise InvalidRequest(
                f"ttl_hours must be between {MIN_TTL_HOURS} and {spec.max_ttl_hours} "
                f"for template {template}"
            )
        return spec


def build_catalog(modules_dir: Path, *, include_demo: bool) -> Catalog:
    templates = (VM_DATABASE, STATIC_SITE, K8S_NAMESPACE)
    if include_demo:
        templates += (LOCAL_DEMO,)
    return Catalog(templates, modules_dir)
