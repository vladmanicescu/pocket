"""
AWS EKS — HashiCorp Vault (KMS + IRSA + Helm) driven by platform.yaml.

Terraform lives under providers/aws/eks/terraform; pocket writes terraform.tfvars
via the EKS backend and can run Terraform or kubectl for Vault.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import click

from pocket.config import PlatformConfig
from pocket.backends.aws import eks as eks_backend

def terraform_dir() -> Path:
    return eks_backend.terraform_directory()


# Repo: providers/aws/eks/terraform/manifests/eso-cluster-secret-store.yaml
_ESO_CLUSTER_SECRET_STORE = (
    Path(__file__).resolve().parent.parent.parent.parent.parent
    / "providers"
    / "aws"
    / "eks"
    / "terraform"
    / "manifests"
    / "eso-cluster-secret-store.yaml"
)

# Populated by `pocket vault init` (root token + recovery keys JSON) for later `pocket vault token` / bootstrap.
_VAULT_NS = "vault"
_BOOTSTRAP_SECRET = "pocket-vault-bootstrap"


def _ensure_eks(cfg: PlatformConfig) -> None:
    if cfg.kubernetes.backend != "eks":
        click.echo(
            click.style("✗ Vault on AWS is only supported with kubernetes.backend: eks.", fg="red"),
            err=True,
        )
        sys.exit(1)


def _sync_kubeconfig(cfg: PlatformConfig) -> None:
    aws = cfg.kubernetes.aws
    eks = aws.eks if aws else None
    region = aws.region if aws else "eu-central-1"
    name = eks.cluster_name if eks else "pocket-eks"
    cmd = ["aws", "eks", "update-kubeconfig", "--region", region, "--name", name]
    if aws and aws.profile:
        cmd += ["--profile", aws.profile]
    subprocess.run(cmd, check=True)


def write_tfvars(cfg: PlatformConfig) -> Path:
    """Render and write EKS terraform.tfvars (includes vault_*)."""
    return eks_backend.write(cfg)


def plan(cfg: PlatformConfig) -> None:
    """terraform plan for the EKS stack (includes Vault when enabled)."""
    _ensure_eks(cfg)
    write_tfvars(cfg)
    td = terraform_dir()
    _run(["terraform", "init"], cwd=td)
    _run(["terraform", "plan"], cwd=td)


def install(cfg: PlatformConfig) -> None:
    """Terraform apply for the EKS directory only (same as the full infra apply).

    Prefer **`pocket apply --run`** so Vault ships with the cluster in one Terraform
    apply. Use `pocket vault install` only for a follow-up apply without re-running
    the full CLI flow.
    """
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(
            click.style(
                "✗ platform.vault.enabled is false — set vault.enabled: true in platform.yaml to deploy Vault.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)
    write_tfvars(cfg)
    click.echo(click.style("==> Terraform init && apply (EKS + Vault + CSI — same state)", fg="cyan"))
    eks_backend.run_terraform_init_apply()
    click.echo(click.style("✓ Apply complete", fg="green"))
    click.echo(
        "When vault-0 is Ready: pocket vault init (one-time), then pocket vault status"
    )


def operator_init(cfg: PlatformConfig) -> None:
    """Run ``vault operator init -format=json`` in vault-0 and store root token + recovery keys in a Secret."""
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(click.style("✗ Vault is disabled in platform.yaml.", fg="red"), err=True)
        sys.exit(1)
    _sync_kubeconfig(cfg)
    click.echo(click.style("==> vault operator init (JSON) → Kubernetes Secret", fg="cyan"))
    proc = subprocess.run(
        [
            "kubectl",
            "-n",
            _VAULT_NS,
            "exec",
            "vault-0",
            "--",
            "vault",
            "operator",
            "init",
            "-format=json",
        ],
        capture_output=True,
        text=True,
    )
    combined = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode != 0:
        if "already initialized" in combined.lower():
            click.echo(
                click.style("✗ Vault is already initialized.", fg="yellow"),
                err=True,
            )
            if _secret_exists():
                click.echo(
                    f"  Root token is in Secret {_VAULT_NS}/{_BOOTSTRAP_SECRET}. "
                    "Run:  pocket vault token --export"
                )
            else:
                click.echo(
                    "  No pocket bootstrap secret found (init was not done via pocket, or secret was deleted). "
                    "Use recovery keys / org process, or reset Vault storage and run pocket vault init again.",
                    err=True,
                )
            sys.exit(1)
        click.echo(click.style("✗ vault operator init failed.", fg="red"), err=True)
        click.echo(combined.strip(), err=True)
        sys.exit(proc.returncode or 1)

    raw = (proc.stdout or "").strip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        click.echo(click.style("✗ Could not parse init JSON from vault.", fg="red"), err=True)
        click.echo(raw[:2000], err=True)
        sys.exit(1)

    root = payload.get("root_token")
    if not root:
        click.echo(click.style("✗ Init JSON missing root_token.", fg="red"), err=True)
        sys.exit(1)

    recovery = payload.get("recovery_keys") or payload.get("recovery_keys_hex") or []
    if isinstance(recovery, str):
        recovery = [recovery]

    _write_bootstrap_secret(root, recovery)

    click.echo(
        click.style("✓ Vault initialized.", fg="green")
        + f"  Credentials stored in Secret `{_VAULT_NS}/{_BOOTSTRAP_SECRET}` (root token + recovery keys)."
    )
    click.echo(
        f"  Next:  pocket vault bootstrap   "
        f"(uses that secret if {click.style('VAULT_TOKEN', bold=True)} is unset)\n"
        f"  Or:    eval \"$(pocket vault token --export)\"   then CLI/UI as needed"
    )


def token_export(cfg: PlatformConfig) -> None:
    """Print shell export for VAULT_TOKEN from the bootstrap Secret (for eval)."""
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(click.style("✗ Vault is disabled in platform.yaml.", fg="red"), err=True)
        sys.exit(1)
    _sync_kubeconfig(cfg)
    t = _read_root_token_from_secret()
    if not t:
        click.echo(
            click.style(
                f"✗ No root token in Secret {_VAULT_NS}/{_BOOTSTRAP_SECRET}. Run pocket vault init first.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)
    # Safe for POSIX eval: single-quoted token with escaped single quotes
    escaped = t.replace("'", "'\"'\"'")
    click.echo(f"export VAULT_TOKEN='{escaped}'")


def token_show(cfg: PlatformConfig) -> None:
    """Print root token to stdout (for scripts); prefer token --export for shells."""
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(click.style("✗ Vault is disabled in platform.yaml.", fg="red"), err=True)
        sys.exit(1)
    _sync_kubeconfig(cfg)
    t = _read_root_token_from_secret()
    if not t:
        click.echo(
            click.style(
                f"✗ No root token in Secret {_VAULT_NS}/{_BOOTSTRAP_SECRET}. Run pocket vault init first.",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)
    click.echo(t)


def token_info(cfg: PlatformConfig) -> None:
    """Describe where the root token lives (after pocket vault init)."""
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(click.style("✗ Vault is disabled in platform.yaml.", fg="red"), err=True)
        sys.exit(1)
    _sync_kubeconfig(cfg)
    if _read_root_token_from_secret():
        click.echo(
            f"Bootstrap Secret `{_VAULT_NS}/{_BOOTSTRAP_SECRET}` exists (root token + recovery keys)."
        )
        click.echo('  eval "$(pocket vault token --export)"')
        click.echo("  pocket vault token --raw     # token only, for scripts")
        click.echo("  pocket vault bootstrap       # uses the secret if VAULT_TOKEN is unset")
    else:
        click.echo(
            click.style(
                f"✗ No bootstrap Secret. Run pocket vault init (writes {_VAULT_NS}/{_BOOTSTRAP_SECRET}).",
                fg="yellow",
            ),
            err=True,
        )


def status(cfg: PlatformConfig) -> None:
    """Print vault status from the vault-0 pod."""
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(click.style("✗ Vault is disabled in platform.yaml.", fg="red"), err=True)
        sys.exit(1)
    _sync_kubeconfig(cfg)
    subprocess.run(
        ["kubectl", "-n", "vault", "exec", "vault-0", "--", "vault", "status"],
    )


def bootstrap(cfg: PlatformConfig) -> None:
    """Configure KV v2, Kubernetes auth, policy, and role for External Secrets Operator.

    Uses **VAULT_TOKEN** if set; otherwise reads the root token from the Secret written
    by ``pocket vault init`` (``vault/pocket-vault-bootstrap``).

    Run after: ``pocket apply --run``, ``pocket vault init``, and Vault pod is Ready.

    Creates policy ``external-secrets`` and auth role ``external-secrets`` for the
    ESO controller service account in namespace ``external-secrets``, then applies
    the **ClusterSecretStore** manifest (CRDs must exist from Terraform: External
    Secrets Helm release).
    """
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(click.style("✗ Vault is disabled in platform.yaml.", fg="red"), err=True)
        sys.exit(1)
    _sync_kubeconfig(cfg)
    token = os.environ.get("VAULT_TOKEN") or _read_root_token_from_secret()
    if not token:
        click.echo(
            click.style(
                "✗ No VAULT_TOKEN and no bootstrap Secret. "
                "Run pocket vault init, or: export VAULT_TOKEN=...",
                fg="red",
            ),
            err=True,
        )
        sys.exit(1)
    if not os.environ.get("VAULT_TOKEN"):
        click.echo(
            click.style(
                f"→ Using root token from Secret {_VAULT_NS}/{_BOOTSTRAP_SECRET}",
                fg="cyan",
            )
        )

    click.echo(click.style("==> Configuring Vault for External Secrets (Kubernetes auth)", fg="cyan"))

    tok = shlex.quote(token)
    script = f"""set -e
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN={tok}
vault secrets enable -path=secret kv-v2 2>/dev/null || true
vault auth enable kubernetes 2>/dev/null || true
vault write auth/kubernetes/config \\
  kubernetes_host=https://kubernetes.default.svc:443 \\
  kubernetes_ca_cert=@/var/run/secrets/kubernetes.io/serviceaccount/ca.crt \\
  token_reviewer_jwt=@/var/run/secrets/kubernetes.io/serviceaccount/token
echo 'path "secret/data/*" {{ capabilities = ["read"] }}' | vault policy write external-secrets -
vault write auth/kubernetes/role/external-secrets \\
  bound_service_account_names=external-secrets \\
  bound_service_account_namespaces=external-secrets \\
  policies=external-secrets \\
  ttl=24h
"""
    r = subprocess.run(
        ["kubectl", "exec", "-n", "vault", "vault-0", "--", "sh", "-c", script],
    )
    if r.returncode != 0:
        click.echo(click.style("✗ Vault bootstrap failed.", fg="red"), err=True)
        sys.exit(r.returncode)

    if not _ESO_CLUSTER_SECRET_STORE.is_file():
        click.echo(
            click.style(f"✗ Missing manifest: {_ESO_CLUSTER_SECRET_STORE}", fg="red"),
            err=True,
        )
        sys.exit(1)

    click.echo(click.style(f"==> kubectl apply -f {_ESO_CLUSTER_SECRET_STORE.name}", fg="cyan"))
    r2 = subprocess.run(["kubectl", "apply", "-f", str(_ESO_CLUSTER_SECRET_STORE)])
    if r2.returncode != 0:
        click.echo(click.style("✗ kubectl apply ClusterSecretStore failed.", fg="red"), err=True)
        sys.exit(r2.returncode)

    click.echo(click.style("✓ Vault Kubernetes auth + ClusterSecretStore `vault` configured.", fg="green"))
    click.echo(
        "Create an ExternalSecret with secretStoreRef.name: vault (paths under secret/ KV v2)."
    )


def port_forward(cfg: PlatformConfig) -> None:
    """Port-forward Vault to localhost:8200 (blocks until Ctrl+C)."""
    _ensure_eks(cfg)
    if not _vault_enabled(cfg):
        click.echo(click.style("✗ Vault is disabled in platform.yaml.", fg="red"), err=True)
        sys.exit(1)
    _sync_kubeconfig(cfg)
    click.echo(click.style("==> Port-forward vault.vault.svc:8200 → 127.0.0.1:8200", fg="cyan"))
    click.echo("    export VAULT_ADDR=http://127.0.0.1:8200")
    click.echo("    UI: http://127.0.0.1:8200/ui   —   Ctrl+C to stop")
    subprocess.run(
        ["kubectl", "-n", "vault", "port-forward", "svc/vault", "8200:8200"],
    )


def _vault_enabled(cfg: PlatformConfig) -> bool:
    v = cfg.platform.vault
    if v is None:
        return True
    return v.enabled if v.enabled is not None else True


def _secret_exists() -> bool:
    r = subprocess.run(
        ["kubectl", "-n", _VAULT_NS, "get", "secret", _BOOTSTRAP_SECRET],
        capture_output=True,
    )
    return r.returncode == 0


def _read_root_token_from_secret() -> str | None:
    r = subprocess.run(
        [
            "kubectl",
            "-n",
            _VAULT_NS,
            "get",
            "secret",
            _BOOTSTRAP_SECRET,
            "-o",
            "jsonpath={.data.root_token}",
        ],
        capture_output=True,
        text=True,
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        return None
    try:
        return base64.b64decode(r.stdout.strip()).decode()
    except (ValueError, UnicodeDecodeError):
        return None


def read_bootstrap_root_token() -> str | None:
    """Root token from Secret ``vault/pocket-vault-bootstrap`` (after ``pocket vault init``)."""
    return _read_root_token_from_secret()


def _write_bootstrap_secret(root_token: str, recovery_keys: list[str]) -> None:
    subprocess.run(
        ["kubectl", "-n", _VAULT_NS, "delete", "secret", _BOOTSTRAP_SECRET, "--ignore-not-found"],
        capture_output=True,
    )
    recovery_json = json.dumps(recovery_keys)
    cr = subprocess.run(
        [
            "kubectl",
            "-n",
            _VAULT_NS,
            "create",
            "secret",
            "generic",
            _BOOTSTRAP_SECRET,
            f"--from-literal=root_token={root_token}",
            f"--from-literal=recovery_keys={recovery_json}",
        ],
        capture_output=True,
        text=True,
    )
    if cr.returncode != 0:
        click.echo(click.style("✗ Failed to create bootstrap Secret.", fg="red"), err=True)
        click.echo((cr.stderr or cr.stdout or "").strip(), err=True)
        sys.exit(cr.returncode)


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    r = subprocess.run(cmd, cwd=cwd or terraform_dir())
    if r.returncode != 0:
        click.echo(click.style(f"✗ Command failed: {' '.join(cmd)}", fg="red"), err=True)
        sys.exit(r.returncode)
