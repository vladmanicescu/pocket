# pocket CLI — command reference

This document lists **pocket** commands and Makefile helpers for the [devops_assignment](../README.md) repo. Configuration lives in **`platform.yaml`** at the repository root (validated against `platform/schema/v1/platform-config.schema.json`).

For EKS, Vault, and External Secrets in more detail, see [providers/aws/eks/README.md](../providers/aws/eks/README.md).

---

## Install and run

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e .
```

Alternative (uses `uv`):

```bash
make dev-setup
source .venv/bin/activate
```

Scaffold a starter config:

```bash
pocket init --backend eks -o platform.yaml
# or: pocket init --backend vanilla -o platform.yaml
```

---

## Global options

| Option | Description |
|--------|------------|
| `--config PATH`, `-c` | Path to `platform.yaml` (default: `platform.yaml`). Relative paths are resolved from the current directory and parent directories so you can run **pocket** from subfolders. |

Examples:

```bash
pocket validate
pocket -c platform.yaml apply --run
```

---

## Core commands

| Command | Description |
|---------|------------|
| `pocket validate` | Validate `platform.yaml` against the JSON Schema. |
| `pocket plan` | Dry-run: show what would be written to `terraform.tfvars` (no disk write). |
| `pocket apply` | Render and write `terraform.tfvars` for the active backend (`providers/aws/eks/terraform` for EKS). |
| `pocket apply --run` | After writing tfvars: **EKS** — `terraform init` and `terraform apply` in the EKS Terraform directory. **Vanilla** — runs `make infra`. |
| `pocket destroy` | Prompt for confirmation, write tfvars, then **EKS** — `terraform destroy`; **Vanilla** — `make destroy`. |

---

## Vault (`pocket vault`)

Vault is provisioned with the **same Terraform stack** as EKS (when `platform.vault` is enabled). Subcommands:

| Command | Description |
|---------|------------|
| `pocket vault plan` | `terraform plan` in the EKS Terraform directory. |
| `pocket vault install` | Write tfvars and `terraform apply` (includes Vault when enabled). |
| `pocket vault init` | One-time: Vault operator init (JSON) and store root token and recovery keys in Secret `vault/pocket-vault-bootstrap`. |
| `pocket vault token` | Explain where the bootstrap secret lives; optional `--export` (shell `export VAULT_TOKEN=…`) or `--raw` (token only). |
| `pocket vault status` | `vault status` against the in-cluster Vault. |
| `pocket vault bootstrap` | Configure Kubernetes auth and External Secrets integration (uses `VAULT_TOKEN` or the bootstrap Secret). |
| `pocket vault port-forward` | Port-forward Vault to `http://127.0.0.1:8200` until Ctrl+C. |

Typical sequence after the cluster exists:

```bash
pocket vault init
eval "$(pocket vault token --export)"   # optional if not relying on the bootstrap Secret
pocket vault bootstrap
```

---

## GitLab on EKS (`pocket gitlab`)

Requires `kubernetes.backend: eks` and `platform.gitlab.enabled: true` with `install_mode: helm` in `platform.yaml`.

| Command | Description |
|---------|------------|
| `pocket gitlab install` | Install nginx ingress (NLB), optional TLS, then GitLab via Helm (long-running). |
| `pocket gitlab url` | Print the GitLab base URL. |
| `pocket gitlab uninstall` | Confirm, then remove GitLab and related Helm releases (ingress / cert-manager as implemented). |

**Note:** GitLab on the **vanilla** stack is **not** handled by `pocket gitlab`; use Ansible targets such as `make gitlab` (see the main [README](../README.md)).

---

## Makefile targets (EKS + pocket)

From the repo root, with `.venv` set up and `pocket` at `.venv/bin/pocket` (or `POCKET` overridden):

| Target | Description |
|--------|------------|
| `make dev-setup` | Create venv and `pip install -e ".[dev]"`. |
| `make infra-eks` | `pocket apply --config platform.yaml`, then `terraform init` and `terraform apply` under `providers/aws/eks/terraform`. |
| `make destroy-eks` | `pocket apply --config platform.yaml`, then `terraform destroy` in the EKS directory. |
| `make fmt-eks` | `terraform fmt -recursive` in the EKS Terraform directory. |
| `make validate-eks` | `terraform validate` in the EKS Terraform directory. |

---

## End-to-end example (EKS)

```bash
pocket validate
pocket apply --run
aws eks update-kubeconfig --region <region> --name <cluster> --profile <profile>
pocket vault init
pocket vault bootstrap
pocket gitlab install
pocket gitlab url
```

---

## Related files

- [platform.yaml](../platform.yaml) — active config (or copy from [platform/schema/v1/examples/aws-eks.yaml](../platform/schema/v1/examples/aws-eks.yaml)).
- [src/pocket/cli.py](../src/pocket/cli.py) — CLI entrypoint and help text.
- [providers/aws/eks/README.md](../providers/aws/eks/README.md) — EKS, Vault, and operational notes.
