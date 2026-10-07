import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from portal.catalog import Catalog, EnvironmentSpec, TemplateSpec
from portal.terraform.base import Workspace

AWS_PROVIDER = {"source": "hashicorp/aws", "version": "~> 5.70"}
KUBERNETES_PROVIDER = {"source": "hashicorp/kubernetes", "version": "~> 2.33"}


class StateBackend(Protocol):
    def block(self, state_key: str) -> dict[str, Any]: ...


@dataclass(frozen=True)
class S3StateBackend:
    bucket: str
    region: str
    lock_table: str

    def block(self, state_key: str) -> dict[str, Any]:
        return {
            "s3": {
                "bucket": self.bucket,
                "key": state_key,
                "region": self.region,
                "dynamodb_table": self.lock_table,
                "encrypt": True,
            }
        }


@dataclass(frozen=True)
class LocalStateBackend:
    root: Path

    def block(self, state_key: str) -> dict[str, Any]:
        path = self.root.resolve() / state_key
        path.parent.mkdir(parents=True, exist_ok=True)
        return {"local": {"path": path.as_posix()}}


@dataclass(frozen=True)
class ProviderSettings:
    aws_region: str = ""
    provisioner_role_arns: dict[str, str] = field(default_factory=dict)
    template_inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    eks_cluster_name: str = ""


def state_key_for(environment_id: str) -> str:
    return f"environments/{environment_id}/terraform.tfstate"


class WorkspaceManager:
    """Renders one root module per environment that calls the template's library module."""

    def __init__(
        self,
        root: Path,
        backend: StateBackend,
        catalog: Catalog,
        providers: ProviderSettings | None = None,
    ) -> None:
        self._root = root
        self._backend = backend
        self._catalog = catalog
        self._providers = providers or ProviderSettings()

    def prepare(self, spec: EnvironmentSpec) -> Workspace:
        path = (self._root / spec.id).resolve()
        path.mkdir(parents=True, exist_ok=True)
        state_key = state_key_for(spec.id)
        root_module = self.render(spec, self._module_source(spec, path), state_key)
        (path / "main.tf.json").write_text(json.dumps(root_module, indent=2), encoding="utf-8")
        return Workspace(path=path, state_key=state_key)

    def remove(self, environment_id: str) -> None:
        shutil.rmtree(self._root / environment_id, ignore_errors=True)

    def render(self, spec: EnvironmentSpec, module_source: str, state_key: str) -> dict[str, Any]:
        template = self._catalog.get(spec.template)
        root: dict[str, Any] = {
            "terraform": {
                "required_version": ">= 1.9.0",
                "backend": self._backend.block(state_key),
            },
            "module": {
                "environment": {
                    "source": module_source,
                    "environment_id": spec.id,
                    "name": spec.name,
                    "owner": spec.owner,
                    "expires_at": spec.expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "size": spec.size,
                    **self._providers.template_inputs.get(spec.template, {}),
                }
            },
            "output": {
                name: {"value": f"${{module.environment.{name}}}"} for name in template.outputs
            },
        }
        self._add_providers(root, template)
        return root

    def _add_providers(self, root: dict[str, Any], template: TemplateSpec) -> None:
        required: dict[str, Any] = {}
        providers: dict[str, Any] = {}
        if "aws" in template.providers:
            required["aws"] = AWS_PROVIDER
            providers["aws"] = self._aws_provider(template)
        if "kubernetes" in template.providers:
            required["kubernetes"] = KUBERNETES_PROVIDER
            cluster = {"name": self._providers.eks_cluster_name}
            root["data"] = {
                "aws_eks_cluster": {"target": cluster},
                "aws_eks_cluster_auth": {"target": cluster},
            }
            providers["kubernetes"] = {
                "host": "${data.aws_eks_cluster.target.endpoint}",
                "cluster_ca_certificate": (
                    "${base64decode(data.aws_eks_cluster.target.certificate_authority[0].data)}"
                ),
                "token": "${data.aws_eks_cluster_auth.target.token}",
            }
        if required:
            root["terraform"]["required_providers"] = required
            root["provider"] = providers

    def _aws_provider(self, template: TemplateSpec) -> dict[str, Any]:
        provider: dict[str, Any] = {"region": self._providers.aws_region}
        role_arn = self._providers.provisioner_role_arns.get(template.name)
        if role_arn:
            provider["assume_role"] = {"role_arn": role_arn, "session_name": "env-portal"}
        return provider

    def _module_source(self, spec: EnvironmentSpec, workspace: Path) -> str:
        module = self._catalog.module_path(spec.template, spec.module_version).resolve()
        try:
            # Terraform only treats sources starting with ./ or ../ as plain local paths.
            relative = Path(os.path.relpath(module, workspace)).as_posix()
        except ValueError:
            # Different drive on Windows: no relative path exists.
            return module.as_posix()
        return relative if relative.startswith(".") else f"./{relative}"
