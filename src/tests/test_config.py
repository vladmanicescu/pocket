"""
Tests for pocket.config — covers every important validation path:

  - File / schema loading errors
  - Top-level required fields
  - apiVersion / kind constraints
  - provider enum
  - kubernetes block (backend enum, version pattern)
  - metadata block (name required, non-empty)
  - security_groups block (field validation, all_cidrs property)
  - gitlab block (install_mode enum)
  - Happy-path: minimal vanilla, minimal EKS, full vanilla, full EKS
"""

from __future__ import annotations

import copy
import pathlib

import pytest
import yaml

import pocket.config as cfg_module
from pocket.config import ConfigError, PlatformConfig, SecurityGroups, load
from tests.conftest import write_yaml


# ---------------------------------------------------------------------------
# File / schema loading errors
# ---------------------------------------------------------------------------

class TestFileErrors:
    def test_missing_config_file(self, tmp_path):
        with pytest.raises(ConfigError, match="Config file not found"):
            load(tmp_path / "does-not-exist.yaml")

    def test_missing_schema_file(self, tmp_path, vanilla_dict, monkeypatch):
        monkeypatch.setattr(cfg_module, "_SCHEMA_PATH", pathlib.Path("/nonexistent/schema.json"))
        p = write_yaml(tmp_path, vanilla_dict)
        with pytest.raises(ConfigError, match="Schema file not found"):
            load(p)

    def test_invalid_yaml_syntax(self, tmp_path):
        p = tmp_path / "platform.yaml"
        p.write_text("key: [unclosed\nnot: valid: yaml:")
        with pytest.raises(ConfigError, match="Failed to parse YAML"):
            load(p)

    def test_yaml_not_a_mapping(self, tmp_path):
        p = tmp_path / "platform.yaml"
        p.write_text("- item1\n- item2\n")
        with pytest.raises(ConfigError, match="YAML mapping"):
            load(p)


# ---------------------------------------------------------------------------
# Top-level required fields
# ---------------------------------------------------------------------------

class TestRequiredTopLevelFields:
    @pytest.mark.parametrize("missing_key", [
        "apiVersion", "kind", "metadata", "provider", "kubernetes", "platform",
    ])
    def test_missing_field_raises(self, tmp_path, vanilla_dict, missing_key):
        del vanilla_dict[missing_key]
        p = write_yaml(tmp_path, vanilla_dict)
        with pytest.raises(ConfigError, match="validation failed"):
            load(p)


# ---------------------------------------------------------------------------
# apiVersion / kind constraints
# ---------------------------------------------------------------------------

class TestApiVersionKind:
    def test_wrong_api_version(self, tmp_path, vanilla_dict):
        vanilla_dict["apiVersion"] = "platform.dev/v2"
        p = write_yaml(tmp_path, vanilla_dict)
        with pytest.raises(ConfigError, match="validation failed"):
            load(p)

    def test_wrong_kind(self, tmp_path, vanilla_dict):
        vanilla_dict["kind"] = "SomethingElse"
        p = write_yaml(tmp_path, vanilla_dict)
        with pytest.raises(ConfigError, match="validation failed"):
            load(p)


# ---------------------------------------------------------------------------
# metadata block
# ---------------------------------------------------------------------------

class TestMetadataBlock:
    def test_missing_name(self, tmp_path, vanilla_dict):
        vanilla_dict["metadata"] = {}
        p = write_yaml(tmp_path, vanilla_dict)
        with pytest.raises(ConfigError, match="validation failed"):
            load(p)

    def test_empty_name(self, tmp_path, vanilla_dict):
        vanilla_dict["metadata"]["name"] = ""
        p = write_yaml(tmp_path, vanilla_dict)
        with pytest.raises(ConfigError, match="validation failed"):
            load(p)

    def test_optional_environment_and_labels(self, tmp_path, vanilla_dict):
        vanilla_dict["metadata"]["environment"] = "prod"
        vanilla_dict["metadata"]["labels"] = {"team": "platform"}
        p = write_yaml(tmp_path, vanilla_dict)
        cfg = load(p)
        assert cfg.metadata.environment == "prod"
        assert cfg.metadata.labels == {"team": "platform"}


# ---------------------------------------------------------------------------
# security_groups block (vanilla)
# ---------------------------------------------------------------------------

class TestSecurityGroupsBlock:
    def _vanilla_with_sg(self, vanilla_dict, sg: dict) -> dict:
        vanilla_dict["kubernetes"]["aws"] = {
            "region": "eu-central-1",
            "vanilla": {"security_groups": sg},
        }
        return vanilla_dict

    def test_valid_security_groups(self, tmp_path, vanilla_dict):
        data = self._vanilla_with_sg(vanilla_dict, {
            "ssh_cidrs": ["10.0.0.0/8"],
            "k8s_api_cidrs": ["10.0.0.0/8"],
            "http_cidrs": ["0.0.0.0/0"],
        })
        p = write_yaml(tmp_path, data)
        cfg = load(p)
        sg = cfg.kubernetes.aws.vanilla.security_groups
        assert sg.ssh_cidrs == ["10.0.0.0/8"]
        assert sg.http_cidrs == ["0.0.0.0/0"]

    def test_unknown_security_group_field_rejected(self, tmp_path, vanilla_dict):
        data = self._vanilla_with_sg(vanilla_dict, {
            "ssh_cidrs": ["10.0.0.0/8"],
            "unknown_field": ["bad"],
        })
        p = write_yaml(tmp_path, data)
        with pytest.raises(ConfigError, match="validation failed"):
            load(p)

    def test_partial_security_groups_allowed(self, tmp_path, vanilla_dict):
        data = self._vanilla_with_sg(vanilla_dict, {"ssh_cidrs": ["10.0.0.0/8"]})
        p = write_yaml(tmp_path, data)
        cfg = load(p)
        sg = cfg.kubernetes.aws.vanilla.security_groups
        assert sg.k8s_api_cidrs is None
        assert sg.http_cidrs is None


# ---------------------------------------------------------------------------
# SecurityGroups.all_cidrs property
# ---------------------------------------------------------------------------

class TestAllCidrsProperty:
    def test_deduplicates_across_groups(self):
        sg = SecurityGroups(
            ssh_cidrs=["10.0.0.0/8", "192.168.0.0/16"],
            k8s_api_cidrs=["10.0.0.0/8"],
            http_cidrs=["0.0.0.0/0"],
        )
        assert sg.all_cidrs == ["10.0.0.0/8", "192.168.0.0/16", "0.0.0.0/0"]

    def test_defaults_to_open_when_empty(self):
        assert SecurityGroups().all_cidrs == ["0.0.0.0/0"]

    def test_partial_groups(self):
        sg = SecurityGroups(ssh_cidrs=["10.0.0.0/8"])
        assert sg.all_cidrs == ["10.0.0.0/8"]

    def test_preserves_insertion_order(self):
        sg = SecurityGroups(
            ssh_cidrs=["1.0.0.0/8"],
            k8s_api_cidrs=["2.0.0.0/8"],
            http_cidrs=["3.0.0.0/8"],
        )
        assert sg.all_cidrs == ["1.0.0.0/8", "2.0.0.0/8", "3.0.0.0/8"]


# ---------------------------------------------------------------------------
# GitLab block
# ---------------------------------------------------------------------------

class TestGitLabBlock:
    def test_invalid_install_mode(self, tmp_path, vanilla_dict):
        vanilla_dict["platform"] = {
            "gitlab": {"enabled": True, "install_mode": "bare_metal"},
        }
        p = write_yaml(tmp_path, vanilla_dict)
        with pytest.raises(ConfigError, match="validation failed"):
            load(p)

    @pytest.mark.parametrize("mode", ["omnibus_vm", "helm", "external"])
    def test_valid_install_modes(self, tmp_path, vanilla_dict, mode):
        vanilla_dict["platform"] = {"gitlab": {"enabled": True, "install_mode": mode}}
        p = write_yaml(tmp_path, vanilla_dict)
        cfg = load(p)
        assert cfg.platform.gitlab.install_mode == mode


# ---------------------------------------------------------------------------
# Happy path — full valid configs
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_minimal_vanilla(self, tmp_path, vanilla_dict):
        p = write_yaml(tmp_path, vanilla_dict)
        cfg = load(p)
        assert isinstance(cfg, PlatformConfig)
        assert cfg.kubernetes.backend == "vanilla"
        assert cfg.metadata.name == "test-stack"

    def test_minimal_eks(self, tmp_path, eks_dict):
        p = write_yaml(tmp_path, eks_dict)
        cfg = load(p)
        assert cfg.kubernetes.backend == "eks"

    def test_full_vanilla(self, tmp_path, vanilla_dict):
        vanilla_dict["kubernetes"]["network"] = {
            "vpc_cidr": "172.31.0.0/16",
            "subnet_cidr": "172.31.1.0/24",
            "availability_zone": "eu-central-1a",
        }
        vanilla_dict["kubernetes"]["aws"] = {
            "region": "eu-central-1",
            "profile": "default",
            "vanilla": {
                "key_name": "my-key",
                "instance_type": "t3.medium",
                "security_groups": {
                    "ssh_cidrs": ["10.0.0.0/8"],
                    "k8s_api_cidrs": ["10.0.0.0/8"],
                    "http_cidrs": ["0.0.0.0/0"],
                },
                "nodes": [
                    {
                        "name": "cp1", "hostname": "cp1",
                        "private_ip": "172.31.1.11",
                        "gateway": "172.31.1.1",
                        "extra_disk_size": 10,
                    },
                ],
                "nfs_instance": {
                    "instance_type": "t3.small",
                    "private_ip": "172.31.1.20",
                    "root_volume_gb": 20,
                },
                "gitlab_instance": {
                    "instance_type": "t3.large",
                    "private_ip": "172.31.1.30",
                    "root_volume_gb": 50,
                },
            },
        }
        vanilla_dict["platform"] = {
            "ingress": {"class": "nginx"},
            "storage": {
                "nfs": {
                    "enabled": True,
                    "server_host": "172.31.1.20",
                    "export_path": "/srv/nfs/k8s",
                },
                "default_storage_class": "nfs-client",
            },
            "gitlab": {
                "enabled": True,
                "install_mode": "omnibus_vm",
                "bootstrap": {"enabled": True, "projects": ["python-auth-app"]},
            },
            "applications": [
                {"name": "python-auth", "chart": "app/python-auth-k8s", "namespace": "default"},
            ],
        }
        p = write_yaml(tmp_path, vanilla_dict)
        cfg = load(p)

        assert cfg.kubernetes.aws.region == "eu-central-1"
        assert cfg.kubernetes.aws.vanilla.nodes[0].name == "cp1"
        assert cfg.kubernetes.aws.vanilla.security_groups.ssh_cidrs == ["10.0.0.0/8"]
        assert cfg.kubernetes.aws.vanilla.nfs_instance.root_volume_gb == 20
        assert cfg.kubernetes.aws.vanilla.gitlab_instance.private_ip == "172.31.1.30"
        assert cfg.platform.gitlab.install_mode == "omnibus_vm"
        assert cfg.platform.gitlab.bootstrap.projects == ["python-auth-app"]
        assert cfg.platform.storage.nfs.enabled is True
        assert cfg.platform.ingress.ingress_class == "nginx"
        assert cfg.platform.applications[0].name == "python-auth"

    def test_full_eks(self, tmp_path, eks_dict):
        eks_dict["kubernetes"]["network"] = {
            "vpc_cidr": "10.40.0.0/16",
            "availability_zone": "eu-central-1a",
        }
        eks_dict["kubernetes"]["aws"] = {
            "region": "eu-central-1",
            "profile": "default",
            "eks": {
                "cluster_name": "pocket-eks",
                "endpoint_public_access": True,
                "endpoint_private_access": True,
                "single_nat_gateway": True,
                "node_instance_types": ["t3.medium"],
                "node_desired_size": 2,
            },
        }
        eks_dict["platform"] = {
            "gitlab": {"enabled": False},
            "storage": {"nfs": {"enabled": False}},
        }
        p = write_yaml(tmp_path, eks_dict)
        cfg = load(p)

        assert cfg.kubernetes.aws.eks.cluster_name == "pocket-eks"
        assert cfg.kubernetes.aws.eks.endpoint_public_access is True
        assert cfg.kubernetes.aws.eks.node_instance_types == ["t3.medium"]
        assert cfg.platform.gitlab.enabled is False
