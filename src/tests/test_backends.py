"""
Tests for pocket.backends — covers:

  hcl.py
    - string, boolean, number, string_list, object_list, assignment primitives
    - escaping, edge cases (empty list, None values skipped, alignment)

  vanilla.py  (render only — write() tested via tmp_path)
    - minimal config produces required variables
    - optional fields absent → not in output
    - full config → all variables present with correct HCL format
    - vm_definitions rendered as object list
    - security groups use all_cidrs
    - write() creates the file at the given path

  eks.py  (render only — write() tested via tmp_path)
    - minimal config produces required variables
    - vpc_cidr prefers eks block over network block
    - vpc_cidr falls back to network block
    - booleans rendered as lowercase true/false
    - full config → all variables present
    - write() creates the file at the given path
"""

from __future__ import annotations

import pathlib

import pytest

from pocket.backends.aws import hcl
from pocket.backends.aws import vanilla as vanilla_backend
from pocket.backends.aws import eks as eks_backend
from pocket.config import (
    AwsConfig, EksConfig, Kubernetes, Metadata, Network,
    PlatformConfig, PlatformServices, SecurityGroups, Vault,
    VanillaConfig, VanillaNode, NfsHost, GitLabHost,
)


# ---------------------------------------------------------------------------
# Helpers — build PlatformConfig objects without touching disk or schema
# ---------------------------------------------------------------------------

def _base_cfg(**k8s_kwargs) -> PlatformConfig:
    """Minimal valid PlatformConfig for render tests."""
    return PlatformConfig(
        apiVersion="platform.dev/v1",
        kind="PlatformConfig",
        metadata=Metadata(name="test"),
        provider="aws",
        kubernetes=Kubernetes(backend="vanilla", version="1.31", **k8s_kwargs),
        platform=PlatformServices(),
    )


def _vanilla_cfg(vanilla: VanillaConfig | None = None, network: Network | None = None,
                 profile: str | None = None) -> PlatformConfig:
    aws = AwsConfig(region="eu-central-1", profile=profile, vanilla=vanilla)
    return _base_cfg(aws=aws, network=network)


def _eks_cfg(eks: EksConfig | None = None, network: Network | None = None,
             profile: str | None = None, vault: Vault | None = None) -> PlatformConfig:
    aws = AwsConfig(region="eu-central-1", profile=profile, eks=eks)
    platform = PlatformServices(vault=vault) if vault is not None else PlatformServices()
    return PlatformConfig(
        apiVersion="platform.dev/v1",
        kind="PlatformConfig",
        metadata=Metadata(name="test"),
        provider="aws",
        kubernetes=Kubernetes(backend="eks", version="1.31", aws=aws, network=network),
        platform=platform,
    )


# ---------------------------------------------------------------------------
# hcl — primitives
# ---------------------------------------------------------------------------

class TestHclString:
    def test_simple(self):
        assert hcl.string("hello") == '"hello"'

    def test_escapes_double_quotes(self):
        assert hcl.string('say "hi"') == r'"say \"hi\""'

    def test_escapes_backslash(self):
        assert hcl.string("a\\b") == '"a\\\\b"'

    def test_empty_string(self):
        assert hcl.string("") == '""'


class TestHclBoolean:
    def test_true(self):
        assert hcl.boolean(True) == "true"

    def test_false(self):
        assert hcl.boolean(False) == "false"


class TestHclNumber:
    def test_integer(self):
        assert hcl.number(42) == "42"

    def test_float(self):
        assert hcl.number(3.14) == "3.14"


class TestHclStringList:
    def test_empty(self):
        assert hcl.string_list([]) == "[]"

    def test_single(self):
        assert hcl.string_list(["0.0.0.0/0"]) == '["0.0.0.0/0"]'

    def test_multiple(self):
        assert hcl.string_list(["a", "b"]) == '["a", "b"]'


class TestHclObjectList:
    def test_empty(self):
        assert hcl.object_list([]) == "[]"

    def test_none_values_skipped(self):
        result = hcl.object_list([{"name": "x", "gateway": None}])
        assert "gateway" not in result
        assert '"x"' in result

    def test_single_row(self):
        result = hcl.object_list([{"name": "cp1", "extra_disk_size": 3}])
        assert '"cp1"' in result
        assert "= 3" in result

    def test_multiple_rows_comma_between(self):
        rows = [
            {"name": "cp1", "extra_disk_size": 3},
            {"name": "w1",  "extra_disk_size": 3},
        ]
        result = hcl.object_list(rows)
        lines = result.splitlines()
        # closing brace of first object should have a comma
        closing_braces = [l for l in lines if l.strip() == "},"]
        assert len(closing_braces) == 1

    def test_last_row_no_trailing_comma(self):
        rows = [{"name": "cp1"}, {"name": "w1"}]
        result = hcl.object_list(rows)
        lines = result.splitlines()
        last_brace = [l for l in lines if "}" in l][-1]
        assert not last_brace.rstrip().endswith(",")

    def test_boolean_values(self):
        result = hcl.object_list([{"enabled": True}])
        assert "= true" in result

    def test_key_alignment(self):
        rows = [{"name": "x", "extra_disk_size": 10}]
        result = hcl.object_list(rows)
        # both keys should be padded to the same width
        name_line = next(l for l in result.splitlines() if "name" in l)
        disk_line = next(l for l in result.splitlines() if "extra_disk_size" in l)
        assert name_line.index("=") == disk_line.index("=")


class TestHclAssignment:
    def test_produces_key_equals_value(self):
        assert hcl.assignment("aws_region", '"eu-central-1"') == 'aws_region = "eu-central-1"'


# ---------------------------------------------------------------------------
# vanilla backend — render()
# ---------------------------------------------------------------------------

class TestVanillaRender:
    def test_minimal_always_has_region_and_project(self):
        out = vanilla_backend.render(_vanilla_cfg())
        assert 'aws_region = "eu-central-1"' in out
        assert 'project_name = "test"' in out

    def test_profile_included_when_set(self):
        out = vanilla_backend.render(_vanilla_cfg(profile="myprofile"))
        assert 'aws_profile = "myprofile"' in out

    def test_profile_absent_when_not_set(self):
        out = vanilla_backend.render(_vanilla_cfg())
        assert "aws_profile" not in out

    def test_network_variables(self):
        net = Network(vpc_cidr="172.31.0.0/16", subnet_cidr="172.31.1.0/24",
                      availability_zone="eu-central-1a")
        out = vanilla_backend.render(_vanilla_cfg(network=net))
        assert 'vpc_cidr = "172.31.0.0/16"' in out
        assert 'subnet_cidr = "172.31.1.0/24"' in out
        assert 'availability_zone = "eu-central-1a"' in out

    def test_optional_network_absent_when_not_set(self):
        out = vanilla_backend.render(_vanilla_cfg())
        assert "vpc_cidr" not in out
        assert "subnet_cidr" not in out
        assert "availability_zone" not in out

    def test_instance_type_and_key_name(self):
        v = VanillaConfig(instance_type="t3.medium", key_name="my-key")
        out = vanilla_backend.render(_vanilla_cfg(vanilla=v))
        assert 'instance_type = "t3.medium"' in out
        assert 'key_name = "my-key"' in out

    def test_security_groups_all_cidrs(self):
        sg = SecurityGroups(ssh_cidrs=["10.0.0.0/8"], http_cidrs=["0.0.0.0/0"])
        v = VanillaConfig(security_groups=sg)
        out = vanilla_backend.render(_vanilla_cfg(vanilla=v))
        assert "allowed_ssh_cidrs" in out
        assert '"10.0.0.0/8"' in out
        assert '"0.0.0.0/0"' in out

    def test_no_security_groups_no_allowed_ssh_cidrs(self):
        out = vanilla_backend.render(_vanilla_cfg(vanilla=VanillaConfig()))
        assert "allowed_ssh_cidrs" not in out

    def test_vm_definitions_rendered(self):
        nodes = [
            VanillaNode(name="cp1", hostname="cp1", private_ip="10.0.1.11",
                        gateway="10.0.1.1", extra_disk_size=10),
            VanillaNode(name="w1",  hostname="w1",  private_ip="10.0.1.12",
                        gateway="10.0.1.1", extra_disk_size=10),
        ]
        v = VanillaConfig(nodes=nodes)
        out = vanilla_backend.render(_vanilla_cfg(vanilla=v))
        assert "vm_definitions" in out
        assert '"cp1"' in out
        assert '"w1"' in out
        assert '"10.0.1.11"' in out

    def test_vm_definitions_absent_when_no_nodes(self):
        out = vanilla_backend.render(_vanilla_cfg(vanilla=VanillaConfig()))
        assert "vm_definitions" not in out

    def test_output_ends_with_newline(self):
        assert vanilla_backend.render(_vanilla_cfg()).endswith("\n")

    def test_write_creates_file(self, tmp_path):
        dest = tmp_path / "terraform.tfvars"
        returned = vanilla_backend.write(_vanilla_cfg(), path=dest)
        assert returned == dest
        assert dest.exists()
        assert 'aws_region' in dest.read_text()


# ---------------------------------------------------------------------------
# eks backend — render()
# ---------------------------------------------------------------------------

class TestEksRender:
    def test_minimal_has_required_fields(self):
        out = eks_backend.render(_eks_cfg())
        assert 'aws_region = "eu-central-1"' in out
        assert 'project_name = "test"' in out
        assert 'cluster_version = "1.31"' in out
        assert "vault_enabled = true" in out

    def test_profile_included_when_set(self):
        out = eks_backend.render(_eks_cfg(profile="myprofile"))
        assert 'aws_profile = "myprofile"' in out

    def test_profile_absent_when_not_set(self):
        out = eks_backend.render(_eks_cfg())
        assert "aws_profile" not in out

    def test_cluster_name(self):
        eks = EksConfig(cluster_name="pocket-eks")
        out = eks_backend.render(_eks_cfg(eks=eks))
        assert 'cluster_name = "pocket-eks"' in out

    def test_vpc_cidr_from_eks_block(self):
        eks = EksConfig(vpc_cidr="10.40.0.0/16")
        out = eks_backend.render(_eks_cfg(eks=eks))
        assert 'vpc_cidr = "10.40.0.0/16"' in out

    def test_vpc_cidr_fallback_to_network(self):
        net = Network(vpc_cidr="10.50.0.0/16")
        out = eks_backend.render(_eks_cfg(network=net))
        assert 'vpc_cidr = "10.50.0.0/16"' in out

    def test_vpc_cidr_eks_takes_precedence_over_network(self):
        eks = EksConfig(vpc_cidr="10.40.0.0/16")
        net = Network(vpc_cidr="10.99.0.0/16")
        out = eks_backend.render(_eks_cfg(eks=eks, network=net))
        assert '"10.40.0.0/16"' in out
        assert '"10.99.0.0/16"' not in out

    def test_booleans_rendered_lowercase(self):
        eks = EksConfig(endpoint_public_access=True, endpoint_private_access=False,
                        single_nat_gateway=True)
        out = eks_backend.render(_eks_cfg(eks=eks))
        assert "cluster_endpoint_public_access = true" in out
        assert "cluster_endpoint_private_access = false" in out
        assert "single_nat_gateway = true" in out
        assert "True" not in out
        assert "False" not in out

    def test_node_instance_types(self):
        eks = EksConfig(node_instance_types=["t3.medium", "t3.large"])
        out = eks_backend.render(_eks_cfg(eks=eks))
        assert 'node_instance_types = ["t3.medium", "t3.large"]' in out

    def test_node_desired_size(self):
        eks = EksConfig(node_desired_size=3)
        out = eks_backend.render(_eks_cfg(eks=eks))
        assert "node_desired_size = 3" in out

    def test_vault_disabled(self):
        out = eks_backend.render(_eks_cfg(vault=Vault(enabled=False)))
        assert "vault_enabled = false" in out

    def test_vault_replicas_and_storage(self):
        out = eks_backend.render(
            _eks_cfg(vault=Vault(enabled=True, replicas=3, data_storage_size="20Gi"))
        )
        assert "vault_enabled = true" in out
        assert "vault_replicas = 3" in out
        assert 'vault_data_storage_size = "20Gi"' in out

    def test_optional_fields_absent_when_not_set(self):
        out = eks_backend.render(_eks_cfg())
        assert "cluster_name" not in out
        assert "vpc_cidr" not in out
        assert "single_nat_gateway" not in out
        assert "node_instance_types" not in out

    def test_output_ends_with_newline(self):
        assert eks_backend.render(_eks_cfg()).endswith("\n")

    def test_write_creates_file(self, tmp_path):
        dest = tmp_path / "terraform.tfvars"
        returned = eks_backend.write(_eks_cfg(), path=dest)
        assert returned == dest
        assert dest.exists()
        assert "aws_region" in dest.read_text()
