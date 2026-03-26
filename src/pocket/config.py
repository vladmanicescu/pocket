"""
Load and validate platform.yaml, then expose it as typed Pydantic models.

Validation flow:
  1. Parse YAML → plain dict
  2. Validate dict against platform-config.schema.json via jsonschema
     (schema file is the single source of truth — keep it in sync with models)
  3. Deserialise into PlatformConfig for type-safe access in backends
"""

from __future__ import annotations

import json
import pathlib
from typing import Literal, Optional

import jsonschema
import yaml
from pydantic import BaseModel, Field

# Resolve schema path relative to this file so the package works from any cwd
_SCHEMA_PATH = (
    pathlib.Path(__file__).parent.parent.parent
    / "platform" / "schema" / "v1" / "platform-config.schema.json"
)


# ---------------------------------------------------------------------------
# Pydantic models — mirror the JSON Schema structure
# ---------------------------------------------------------------------------

class Metadata(BaseModel):
    name: str
    environment: Optional[str] = None
    labels: Optional[dict[str, str]] = None


class Network(BaseModel):
    vpc_cidr: Optional[str] = None
    subnet_cidr: Optional[str] = None
    availability_zone: Optional[str] = None


class VanillaNode(BaseModel):
    name: str
    hostname: str
    private_ip: str
    gateway: Optional[str] = None
    extra_disk_size: int


class NfsHost(BaseModel):
    instance_type: Optional[str] = None
    private_ip: Optional[str] = None
    root_volume_gb: Optional[int] = None


class GitLabHost(BaseModel):
    instance_type: Optional[str] = None
    private_ip: Optional[str] = None
    root_volume_gb: Optional[int] = None


class SecurityGroups(BaseModel):
    """Per-service CIDR rules for the vanilla security group.

    Each list maps to a dedicated ingress rule; if omitted the Terraform
    default (0.0.0.0/0) applies for that rule.
    """
    ssh_cidrs: Optional[list[str]] = None
    k8s_api_cidrs: Optional[list[str]] = None
    http_cidrs: Optional[list[str]] = None

    @property
    def all_cidrs(self) -> list[str]:
        """Flattened, deduplicated union — used where Terraform expects a
        single allowed_ssh_cidrs list (legacy variable)."""
        seen: set[str] = set()
        result: list[str] = []
        for group in (self.ssh_cidrs, self.k8s_api_cidrs, self.http_cidrs):
            for cidr in (group or []):
                if cidr not in seen:
                    seen.add(cidr)
                    result.append(cidr)
        return result or ["0.0.0.0/0"]


class VanillaConfig(BaseModel):
    key_name: Optional[str] = None
    instance_type: Optional[str] = None
    security_groups: Optional[SecurityGroups] = None
    nodes: Optional[list[VanillaNode]] = None
    nfs_instance: Optional[NfsHost] = None
    gitlab_instance: Optional[GitLabHost] = None


class EksConfig(BaseModel):
    cluster_name: Optional[str] = None
    endpoint_public_access: Optional[bool] = None
    endpoint_private_access: Optional[bool] = None
    vpc_cidr: Optional[str] = None
    node_instance_types: Optional[list[str]] = None
    node_desired_size: Optional[int] = None
    single_nat_gateway: Optional[bool] = None


class AwsConfig(BaseModel):
    region: str
    profile: Optional[str] = None
    vanilla: Optional[VanillaConfig] = None
    eks: Optional[EksConfig] = None


class Kubernetes(BaseModel):
    backend: Literal["vanilla", "eks", "k3d", "local"]
    version: str
    network: Optional[Network] = None
    aws: Optional[AwsConfig] = None


class NfsStorage(BaseModel):
    enabled: Optional[bool] = None
    provisioner_chart: Optional[str] = None
    server_host: Optional[str] = None
    export_path: Optional[str] = None


class Storage(BaseModel):
    nfs: Optional[NfsStorage] = None
    default_storage_class: Optional[str] = None


class Ingress(BaseModel):
    # 'class' is a Python keyword so we alias it
    ingress_class: Optional[str] = Field(None, alias="class")

    model_config = {"populate_by_name": True}


class GitLabRunner(BaseModel):
    enabled: Optional[bool] = None
    concurrent: Optional[int] = None
    job_cpu_request: Optional[str] = None
    job_memory_request: Optional[str] = None
    job_cpu_limit: Optional[str] = None
    job_memory_limit: Optional[str] = None


class GitLabBootstrap(BaseModel):
    enabled: Optional[bool] = None
    projects: Optional[list[str]] = None


class GitLab(BaseModel):
    enabled: Optional[bool] = None
    install_mode: Optional[Literal["omnibus_vm", "helm", "external"]] = None
    hostname: Optional[str] = None
    tls: Optional[bool] = None
    tls_mode: Optional[Literal["letsencrypt", "self_signed"]] = None
    route53_zone_id: Optional[str] = None
    runner: Optional[GitLabRunner] = None
    bootstrap: Optional[GitLabBootstrap] = None

    @property
    def effective_tls(self) -> bool:
        """TLS is active when tls: true is set.
        self_signed mode works without a hostname; letsencrypt requires one."""
        if not self.tls:
            return False
        if self.tls_mode == "self_signed":
            return True
        return bool(self.hostname)  # letsencrypt needs a resolvable hostname

    @property
    def effective_tls_mode(self) -> str:
        """Resolved TLS mode — defaults to letsencrypt when tls_mode is omitted."""
        return self.tls_mode or "letsencrypt"

    @property
    def effective_route53(self) -> bool:
        """Route 53 automation is only active when both hostname and zone ID are set."""
        return bool(self.hostname and self.route53_zone_id)


class Application(BaseModel):
    name: str
    chart: str
    namespace: Optional[str] = None
    values_file: Optional[str] = None


class Vault(BaseModel):
    """HashiCorp Vault on EKS — Terraform (KMS + IRSA + Helm)."""

    enabled: Optional[bool] = None
    replicas: Optional[int] = None
    data_storage_size: Optional[str] = None


class PlatformServices(BaseModel):
    ingress: Optional[Ingress] = None
    storage: Optional[Storage] = None
    vault: Optional[Vault] = None
    gitlab: Optional[GitLab] = None
    applications: Optional[list[Application]] = None


class PlatformConfig(BaseModel):
    apiVersion: str
    kind: str
    metadata: Metadata
    provider: Literal["aws", "azure"]
    kubernetes: Kubernetes
    platform: PlatformServices


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

class ConfigError(Exception):
    """Raised when the config file cannot be loaded or is invalid."""


def load(path: str | pathlib.Path) -> PlatformConfig:
    """
    Load *path* (a platform.yaml), validate it against the JSON Schema,
    and return a fully-typed PlatformConfig.

    Raises ConfigError on any problem so callers get a single exception type.
    """
    config_path = pathlib.Path(path).expanduser().resolve()

    if not config_path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    # 1. Parse YAML
    try:
        raw: dict = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError("Config file must be a YAML mapping at the top level.")

    # 2. JSON Schema validation
    if not _SCHEMA_PATH.exists():
        raise ConfigError(f"Schema file not found: {_SCHEMA_PATH}")

    schema = json.loads(_SCHEMA_PATH.read_text())
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(raw), key=lambda e: list(e.path))

    if errors:
        messages = "\n".join(
            f"  - {'.'.join(str(p) for p in e.path) or '<root>'}: {e.message}"
            for e in errors
        )
        raise ConfigError(f"Config validation failed:\n{messages}")

    # 3. Deserialise into typed models
    try:
        return PlatformConfig.model_validate(raw)
    except Exception as exc:
        raise ConfigError(f"Failed to deserialise config: {exc}") from exc
