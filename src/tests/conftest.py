"""Shared fixtures for pocket tests."""

import copy

import pytest
import yaml


# ---------------------------------------------------------------------------
# Minimal valid config dicts — used as a clean baseline that individual tests
# mutate to exercise a specific failure path.
# ---------------------------------------------------------------------------

MINIMAL_VANILLA: dict = {
    "apiVersion": "platform.dev/v1",
    "kind": "PlatformConfig",
    "metadata": {"name": "test-stack"},
    "provider": "aws",
    "kubernetes": {
        "backend": "vanilla",
        "version": "1.31",
    },
    "platform": {},
}

MINIMAL_EKS: dict = {
    "apiVersion": "platform.dev/v1",
    "kind": "PlatformConfig",
    "metadata": {"name": "test-eks"},
    "provider": "aws",
    "kubernetes": {
        "backend": "eks",
        "version": "1.31",
    },
    "platform": {},
}


@pytest.fixture()
def vanilla_dict() -> dict:
    return copy.deepcopy(MINIMAL_VANILLA)


@pytest.fixture()
def eks_dict() -> dict:
    return copy.deepcopy(MINIMAL_EKS)


def write_yaml(tmp_path, data: dict, filename: str = "platform.yaml"):
    """Write *data* as YAML to *tmp_path/filename* and return the path."""
    p = tmp_path / filename
    p.write_text(yaml.dump(data))
    return p
