"""
EKS backend — renders providers/aws/eks/terraform/terraform.tfvars
from a validated PlatformConfig, and can run Terraform in that directory
(EKS + CSI + Vault in one state).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

from pocket.config import PlatformConfig
from pocket.backends.aws import hcl

_TFVARS_PATH = (
    pathlib.Path(__file__).parent.parent.parent.parent.parent
    / "providers" / "aws" / "eks" / "terraform" / "terraform.tfvars"
)


def terraform_directory() -> pathlib.Path:
    """Directory containing main.tf and terraform.tfvars for the EKS stack."""
    return _TFVARS_PATH.parent


def run_terraform_init_apply() -> None:
    """terraform init && terraform apply -auto-approve (cluster, Vault, CSI, …)."""
    d = terraform_directory()
    for cmd in (["terraform", "init"], ["terraform", "apply", "-auto-approve"]):
        r = subprocess.run(cmd, cwd=d, check=False)
        if r.returncode != 0:
            sys.exit(r.returncode)


def run_terraform_destroy() -> None:
    """terraform destroy -auto-approve."""
    d = terraform_directory()
    r = subprocess.run(
        ["terraform", "destroy", "-auto-approve"],
        cwd=d,
        check=False,
    )
    if r.returncode != 0:
        sys.exit(r.returncode)


def render(cfg: PlatformConfig) -> str:
    """Return the full terraform.tfvars content as a string."""
    aws = cfg.kubernetes.aws
    net = cfg.kubernetes.network
    eks = aws.eks if aws else None

    lines: list[str] = []

    def add(name: str, value: str) -> None:
        lines.append(hcl.assignment(name, value))
        lines.append("")

    # --- provider / region ---
    add("aws_region", hcl.string(aws.region if aws else "eu-central-1"))

    if aws and aws.profile:
        add("aws_profile", hcl.string(aws.profile))

    add("project_name", hcl.string(cfg.metadata.name))

    # --- cluster ---
    if eks and eks.cluster_name:
        add("cluster_name", hcl.string(eks.cluster_name))

    add("cluster_version", hcl.string(cfg.kubernetes.version))

    # --- networking ---
    vpc_cidr = (eks.vpc_cidr if eks and eks.vpc_cidr else None) or (net.vpc_cidr if net else None)
    if vpc_cidr:
        add("vpc_cidr", hcl.string(vpc_cidr))

    if eks and eks.single_nat_gateway is not None:
        add("single_nat_gateway", hcl.boolean(eks.single_nat_gateway))

    # --- endpoint access ---
    if eks and eks.endpoint_public_access is not None:
        add("cluster_endpoint_public_access", hcl.boolean(eks.endpoint_public_access))

    if eks and eks.endpoint_private_access is not None:
        add("cluster_endpoint_private_access", hcl.boolean(eks.endpoint_private_access))

    # --- node group ---
    if eks and eks.node_instance_types:
        add("node_instance_types", hcl.string_list(eks.node_instance_types))

    if eks and eks.node_desired_size is not None:
        add("node_desired_size", hcl.number(eks.node_desired_size))

    # --- Vault (same Terraform module; pocket vault install / apply) ---
    v = cfg.platform.vault
    vault_on = True if v is None else (v.enabled if v.enabled is not None else True)
    add("vault_enabled", hcl.boolean(vault_on))
    if v and v.replicas is not None:
        add("vault_replicas", hcl.number(v.replicas))
    if v and v.data_storage_size:
        add("vault_data_storage_size", hcl.string(v.data_storage_size))

    return "\n".join(lines).rstrip() + "\n"


def write(cfg: PlatformConfig, path: pathlib.Path | None = None) -> pathlib.Path:
    """Render and write terraform.tfvars; return the path written."""
    dest = pathlib.Path(path) if path else _TFVARS_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(cfg))
    return dest
