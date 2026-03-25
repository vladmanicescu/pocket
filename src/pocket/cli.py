"""
pocket CLI — single entrypoint for the platform toolchain.

Usage:
    pocket --config platform.yaml validate
    pocket --config platform.yaml plan
    pocket --config platform.yaml apply [--run]
    pocket --config platform.yaml destroy
"""

from __future__ import annotations

import subprocess
import sys
import pathlib

import click

from pocket.config import ConfigError, load, PlatformConfig
from pocket.backends.aws import vanilla as vanilla_backend
from pocket.backends.aws import eks as eks_backend

# Repo root is three levels above this file (src/pocket/cli.py → repo root)
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()

_EXAMPLES_DIR = (
    pathlib.Path(__file__).parent.parent.parent
    / "platform" / "schema" / "v1" / "examples"
)

_BACKEND_EXAMPLES = {
    "vanilla": _EXAMPLES_DIR / "aws-vanilla.yaml",
    "eks":     _EXAMPLES_DIR / "aws-eks.yaml",
}

_MAKE_TARGETS = {
    "vanilla": {
        "apply":   "infra",
        "destroy": "destroy",
    },
    "eks": {
        "apply":   "infra-eks",
        "destroy": "destroy-eks",
    },
}


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.option(
    "--config", "-c",
    default="platform.yaml",
    show_default=True,
    type=click.Path(exists=False, dir_okay=False, path_type=pathlib.Path),
    help="Path to your platform.yaml config file.",
)
@click.pass_context
def main(ctx: click.Context, config: pathlib.Path) -> None:
    """pocket — materialise Terraform and Ansible configs from platform.yaml."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--backend", "-b",
    type=click.Choice(["vanilla", "eks"], case_sensitive=False),
    default="eks",
    show_default=True,
    help="Which backend example to use as the starting point.",
)
@click.option(
    "--output", "-o",
    default="platform.yaml",
    show_default=True,
    type=click.Path(dir_okay=False, path_type=pathlib.Path),
    help="Destination file to write.",
)
@click.option(
    "--force", "-f",
    is_flag=True,
    default=False,
    help="Overwrite the output file if it already exists.",
)
def init(backend: str, output: pathlib.Path, force: bool) -> None:
    """Scaffold a platform.yaml from the built-in example for the chosen backend."""
    example = _BACKEND_EXAMPLES[backend]

    if not example.exists():
        click.echo(
            click.style(f"✗ Example file not found: {example}", fg="red"), err=True
        )
        sys.exit(1)

    if output.exists() and not force:
        click.echo(
            click.style(f"✗ {output} already exists. Use --force to overwrite.", fg="red"),
            err=True,
        )
        sys.exit(1)

    output.write_text(example.read_text())
    click.echo(
        click.style(f"✓ Written {output}", fg="green")
        + f"  (backend={backend} — edit before running apply)"
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Validate platform.yaml against the JSON Schema."""
    cfg_path: pathlib.Path = ctx.obj["config_path"]
    try:
        cfg = load(cfg_path)
    except ConfigError as exc:
        click.echo(click.style("✗ Validation failed", fg="red"), err=True)
        click.echo(str(exc), err=True)
        sys.exit(1)

    click.echo(
        click.style("✓ Config is valid", fg="green")
        + f"  backend={cfg.kubernetes.backend}"
        + f"  provider={cfg.provider}"
        + f"  name={cfg.metadata.name}"
    )


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

@main.command()
@click.pass_context
def plan(ctx: click.Context) -> None:
    """Dry-run: show what terraform.tfvars would be written (nothing touches disk)."""
    cfg = _load_or_exit(ctx)
    rendered, dest = _render(cfg)

    click.echo(click.style(f"--- Would write: {dest} ---", fg="yellow"))
    click.echo(rendered)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

@main.command()
@click.option(
    "--run", "run_infra",
    is_flag=True,
    default=False,
    help="After writing tfvars, also run the Terraform infra make target.",
)
@click.pass_context
def apply(ctx: click.Context, run_infra: bool) -> None:
    """Render and write terraform.tfvars; optionally provision infra."""
    cfg = _load_or_exit(ctx)
    rendered, dest = _render(cfg)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered)
    click.echo(click.style(f"✓ Wrote {dest}", fg="green"))

    if run_infra:
        target = _make_target(cfg, "apply")
        _run_make(target)


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------

@main.command()
@click.confirmation_option(prompt="This will destroy infrastructure. Are you sure?")
@click.pass_context
def destroy(ctx: click.Context) -> None:
    """Destroy infrastructure (runs the appropriate make destroy target)."""
    cfg = _load_or_exit(ctx)
    target = _make_target(cfg, "destroy")
    _run_make(target)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _load_or_exit(ctx: click.Context) -> PlatformConfig:
    cfg_path: pathlib.Path = ctx.obj["config_path"]
    try:
        return load(cfg_path)
    except ConfigError as exc:
        click.echo(click.style("✗ Config error", fg="red"), err=True)
        click.echo(str(exc), err=True)
        sys.exit(1)


def _render(cfg: PlatformConfig) -> tuple[str, pathlib.Path]:
    """Return (rendered_content, destination_path) for the active backend."""
    backend = cfg.kubernetes.backend
    if backend == "vanilla":
        return vanilla_backend.render(cfg), vanilla_backend._TFVARS_PATH
    if backend == "eks":
        return eks_backend.render(cfg), eks_backend._TFVARS_PATH
    click.echo(
        click.style(f"✗ Backend '{backend}' has no renderer yet.", fg="red"), err=True
    )
    sys.exit(1)


def _make_target(cfg: PlatformConfig, action: str) -> str:
    backend = cfg.kubernetes.backend
    targets = _MAKE_TARGETS.get(backend)
    if not targets:
        click.echo(
            click.style(f"✗ No make targets defined for backend '{backend}'.", fg="red"),
            err=True,
        )
        sys.exit(1)
    return targets[action]


def _run_make(target: str) -> None:
    click.echo(click.style(f"==> make {target}", fg="cyan"))
    result = subprocess.run(
        ["make", target],
        cwd=_REPO_ROOT,
    )
    if result.returncode != 0:
        click.echo(
            click.style(f"✗ make {target} exited with code {result.returncode}", fg="red"),
            err=True,
        )
        sys.exit(result.returncode)
