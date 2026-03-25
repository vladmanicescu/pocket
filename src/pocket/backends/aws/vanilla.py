"""
Vanilla backend — renders providers/aws/vanilla/terraform/terraform.tfvars
from a validated PlatformConfig.
"""

from __future__ import annotations

import pathlib

from pocket.config import PlatformConfig
from pocket.backends.aws import hcl

# Path to the tfvars file relative to the repo root
_TFVARS_PATH = (
    pathlib.Path(__file__).parent.parent.parent.parent
    / "providers" / "aws" / "vanilla" / "terraform" / "terraform.tfvars"
)


def render(cfg: PlatformConfig) -> str:
    """Return the full terraform.tfvars content as a string."""
    aws = cfg.kubernetes.aws
    net = cfg.kubernetes.network
    vanilla = aws.vanilla if aws else None

    lines: list[str] = []

    def add(name: str, value: str) -> None:
        lines.append(hcl.assignment(name, value))
        lines.append("")

    # --- provider / region ---
    add("aws_region", hcl.string(aws.region if aws else "eu-central-1"))

    if aws and aws.profile:
        add("aws_profile", hcl.string(aws.profile))

    add("project_name", hcl.string(cfg.metadata.name))

    # --- networking ---
    if net and net.vpc_cidr:
        add("vpc_cidr", hcl.string(net.vpc_cidr))

    if net and net.subnet_cidr:
        add("subnet_cidr", hcl.string(net.subnet_cidr))

    if net and net.availability_zone:
        add("availability_zone", hcl.string(net.availability_zone))

    # --- compute ---
    if vanilla and vanilla.instance_type:
        add("instance_type", hcl.string(vanilla.instance_type))

    if vanilla and vanilla.key_name:
        add("key_name", hcl.string(vanilla.key_name))

    # --- security groups ---
    if vanilla and vanilla.security_groups:
        add("allowed_ssh_cidrs", hcl.string_list(vanilla.security_groups.all_cidrs))

    # --- vm_definitions (nodes) ---
    if vanilla and vanilla.nodes:
        rows = [
            {
                "name":            node.name,
                "hostname":        node.hostname,
                "private_ip":      node.private_ip,
                "gateway":         node.gateway,
                "extra_disk_size": node.extra_disk_size,
            }
            for node in vanilla.nodes
        ]
        add("vm_definitions", hcl.object_list(rows))

    return "\n".join(lines).rstrip() + "\n"


def write(cfg: PlatformConfig, path: pathlib.Path | None = None) -> pathlib.Path:
    """Render and write terraform.tfvars; return the path written."""
    dest = pathlib.Path(path) if path else _TFVARS_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(cfg))
    return dest
