"""
pocket CLI — single entrypoint for the platform toolchain.

Usage:
    pocket --config platform.yaml validate
    pocket --config platform.yaml plan
    pocket --config platform.yaml apply [--run]
    pocket --config platform.yaml destroy
    pocket --config platform.yaml vault plan|install|init|token|status|bootstrap|port-forward
"""

from __future__ import annotations

import subprocess
import sys
import pathlib

import click

from pocket.config import ConfigError, load, PlatformConfig
from pocket.backends.aws import vanilla as vanilla_backend
from pocket.backends.aws import eks as eks_backend
from pocket.backends.aws import gitlab as gitlab_backend
from pocket.backends.aws import vault as vault_backend

# Repo root is three levels above this file (src/pocket/cli.py → repo root)
_REPO_ROOT = pathlib.Path(__file__).parent.parent.parent.resolve()


def _resolve_config_path(config: pathlib.Path) -> pathlib.Path:
    """Resolve a relative config path: try cwd, then each ancestor (repo-root platform.yaml from subdirs)."""
    p = pathlib.Path(config).expanduser()
    if p.is_absolute():
        return p.resolve()
    cwd = pathlib.Path.cwd()
    for base in [cwd, *cwd.parents]:
        candidate = (base / p).resolve()
        if candidate.exists():
            return candidate
    return (cwd / p).resolve()

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
    ctx.obj["config_path"] = _resolve_config_path(config)


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
    help="After writing tfvars, run Terraform (EKS: init+apply in-repo; vanilla: make infra).",
)
@click.pass_context
def apply(ctx: click.Context, run_infra: bool) -> None:
    """Render and write terraform.tfvars; optionally provision infra.

    **eks:** `--run` runs `terraform init` and `terraform apply` under
    `providers/aws/eks/terraform` (cluster, CSI, gp3, Vault — no make).
    **vanilla:** `--run` runs `make infra`.
    """
    cfg = _load_or_exit(ctx)
    rendered, dest = _render(cfg)

    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered)
    click.echo(click.style(f"✓ Wrote {dest}", fg="green"))

    if run_infra:
        if cfg.kubernetes.backend == "eks":
            click.echo(
                click.style("==> terraform init && apply (EKS + Vault + CSI)", fg="cyan")
            )
            eks_backend.run_terraform_init_apply()
        else:
            target = _make_target(cfg, "apply")
            _run_make(target)


# ---------------------------------------------------------------------------
# destroy
# ---------------------------------------------------------------------------

@main.command()
@click.confirmation_option(prompt="This will destroy infrastructure. Are you sure?")
@click.pass_context
def destroy(ctx: click.Context) -> None:
    """Destroy infrastructure (writes tfvars first, then Terraform or make)."""
    cfg = _load_or_exit(ctx)
    rendered, dest = _render(cfg)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered)
    click.echo(click.style(f"✓ Wrote {dest}", fg="green"))

    if cfg.kubernetes.backend == "eks":
        click.echo(click.style("==> terraform destroy (EKS stack)", fg="cyan"))
        eks_backend.run_terraform_destroy()
    else:
        target = _make_target(cfg, "destroy")
        _run_make(target)


# ---------------------------------------------------------------------------
# gitlab
# ---------------------------------------------------------------------------

@main.group()
@click.pass_context
def gitlab(ctx: click.Context) -> None:
    """GitLab installation and management on EKS."""
    pass


@gitlab.command("install")
@click.pass_context
def gitlab_install(ctx: click.Context) -> None:
    """Install GitLab via Helm on the running EKS cluster."""
    cfg = _load_or_exit(ctx)
    if cfg.kubernetes.backend != "eks":
        click.echo(
            click.style("✗ 'pocket gitlab install' only supports the eks backend.", fg="red"),
            err=True,
        )
        sys.exit(1)
    gitlab_backend.install(cfg)


@gitlab.command("url")
@click.pass_context
def gitlab_url(ctx: click.Context) -> None:
    """Print the GitLab URL."""
    cfg = _load_or_exit(ctx)
    click.echo(gitlab_backend.get_url(cfg))


@gitlab.command("uninstall")
@click.confirmation_option(prompt="This will remove GitLab from the cluster. Are you sure?")
@click.pass_context
def gitlab_uninstall(ctx: click.Context) -> None:
    """Uninstall GitLab and the ingress controller from the cluster."""
    cfg = _load_or_exit(ctx)
    gitlab_backend.uninstall(cfg)


# ---------------------------------------------------------------------------
# vault (EKS — KMS + IRSA + Helm via Terraform)
# ---------------------------------------------------------------------------

@main.group()
@click.pass_context
def vault(ctx: click.Context) -> None:
    """HashiCorp Vault on EKS (same Terraform stack as the cluster)."""
    pass


@vault.command("plan")
@click.pass_context
def vault_plan(ctx: click.Context) -> None:
    """Run terraform plan for the EKS directory (includes Vault when enabled)."""
    cfg = _load_or_exit(ctx)
    vault_backend.plan(cfg)


@vault.command("install")
@click.pass_context
def vault_install(ctx: click.Context) -> None:
    """Write terraform.tfvars and terraform apply (Vault is part of the EKS state)."""
    cfg = _load_or_exit(ctx)
    vault_backend.install(cfg)


@vault.command("init")
@click.pass_context
def vault_init(ctx: click.Context) -> None:
    """One-time: vault operator init (JSON) and store root token + recovery keys in a Secret."""
    cfg = _load_or_exit(ctx)
    vault_backend.operator_init(cfg)


@vault.command("token")
@click.option(
    "--export",
    "shell_export",
    is_flag=True,
    help="Print export VAULT_TOKEN=… suitable for eval.",
)
@click.option(
    "--raw",
    "raw_token",
    is_flag=True,
    help="Print the root token only (for scripts; avoid in shared logs).",
)
@click.pass_context
def vault_token(ctx: click.Context, shell_export: bool, raw_token: bool) -> None:
    """Show where the root token is, or print it (--export / --raw)."""
    cfg = _load_or_exit(ctx)
    if shell_export and raw_token:
        click.echo(click.style("✗ Use either --export or --raw, not both.", fg="red"), err=True)
        sys.exit(1)
    if shell_export:
        vault_backend.token_export(cfg)
    elif raw_token:
        vault_backend.token_show(cfg)
    else:
        vault_backend.token_info(cfg)


@vault.command("status")
@click.pass_context
def vault_status(ctx: click.Context) -> None:
    """Show vault status from the vault-0 pod."""
    cfg = _load_or_exit(ctx)
    vault_backend.status(cfg)


@vault.command("bootstrap")
@click.pass_context
def vault_bootstrap(ctx: click.Context) -> None:
    """Configure Vault Kubernetes auth for External Secrets (VAULT_TOKEN or bootstrap Secret)."""
    cfg = _load_or_exit(ctx)
    vault_backend.bootstrap(cfg)


@vault.command("port-forward")
@click.pass_context
def vault_port_forward(ctx: click.Context) -> None:
    """Forward Vault to http://127.0.0.1:8200 (blocks until Ctrl+C)."""
    cfg = _load_or_exit(ctx)
    vault_backend.port_forward(cfg)


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
