"""
AWS GitLab backend — installs GitLab via Helm on an existing EKS cluster.

Two modes driven by platform.yaml:

  No domain (hostname omitted):
    - nginx ingress controller is installed → AWS NLB is created
    - NLB hostname is discovered and used as GitLab's domain
    - No TLS (HTTP only)

  With domain (hostname set):
    - Same nginx ingress install
    - cert-manager is installed → Let's Encrypt certificate
    - GitLab is installed with HTTPS
    - User must CNAME their hostname to the NLB hostname after install
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import yaml

from pocket.config import PlatformConfig
from pocket.backends.aws.vault import read_bootstrap_root_token


# ---------------------------------------------------------------------------
# Helm / chart constants
# ---------------------------------------------------------------------------

INGRESS_NGINX_REPO   = "https://kubernetes.github.io/ingress-nginx"
CERT_MANAGER_REPO    = "https://charts.jetstack.io"
GITLAB_REPO          = "https://charts.gitlab.io"

INGRESS_NAMESPACE    = "ingress-nginx"
CERT_MANAGER_NS      = "cert-manager"
GITLAB_NAMESPACE     = "gitlab"

# Helm release name used by pocket gitlab install (must match secret/deployment names).
GITLAB_HELM_RELEASE = "gitlab"
RUNNER_SECRET_NAME = f"{GITLAB_HELM_RELEASE}-gitlab-runner-secret"

CERT_MANAGER_VERSION  = "v1.14.5"
LETSENCRYPT_EMAIL     = "admin@example.com"   # overridden by config if added later
LETSENCRYPT_ISSUER    = "letsencrypt-prod"
SELFSIGNED_CA_ISSUER  = "pocket-ca-issuer"
SELFSIGNED_CA_SECRET  = "pocket-ca-secret"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def install(cfg: PlatformConfig) -> None:
    """Full GitLab installation flow for an EKS cluster."""
    aws   = cfg.kubernetes.aws
    eks   = aws.eks if aws else None
    gl    = cfg.platform.gitlab

    if not gl or not gl.enabled:
        _echo("GitLab is disabled in platform.yaml — nothing to do.")
        return

    if gl.install_mode != "helm":
        _echo(f"install_mode is '{gl.install_mode}' — only 'helm' is handled here.")
        sys.exit(1)

    region       = aws.region if aws else "eu-central-1"
    cluster_name = eks.cluster_name if eks else "pocket-eks"

    _echo("==> Updating kubeconfig")
    _run(["aws", "eks", "update-kubeconfig",
          "--region", region,
          "--name", cluster_name])

    _echo("==> Installing nginx ingress controller")
    _helm_upgrade(
        release="ingress-nginx",
        chart="ingress-nginx",
        repo=INGRESS_NGINX_REPO,
        namespace=INGRESS_NAMESPACE,
        extra_sets={"controller.service.type": "LoadBalancer"},
        wait=False,
    )

    _echo("==> Waiting for NLB address (hostname or IP — may take 3–10 min on AWS)…")
    nlb_host = _wait_for_nlb_hostname(INGRESS_NAMESPACE, "ingress-nginx-controller", timeout=600)

    if gl.hostname:
        domain = gl.hostname
        _echo(f"==> Using custom hostname: {domain}")
        if gl.effective_route53:
            _upsert_route53_cname(gl.route53_zone_id, domain, nlb_host)
            _echo(f"==> Waiting for DNS propagation of {domain}…")
            _wait_for_dns(domain, nlb_host)
        else:
            _echo(f"    Add a CNAME manually: {domain} → {nlb_host}")
    else:
        domain = nlb_host
        _echo(f"==> No hostname set — GitLab will be at: http://{nlb_host}")

    if gl.effective_tls:
        _install_cert_manager()
        if gl.effective_tls_mode == "self_signed":
            _install_selfsigned_issuer()

    _echo("==> Installing GitLab via Helm (this takes 5–10 min)")
    values = _build_helm_values(cfg, domain=domain)
    _helm_upgrade(
        release="gitlab",
        chart="gitlab",
        repo=GITLAB_REPO,
        namespace=GITLAB_NAMESPACE,
        values_dict=values,
        timeout="600s",
    )

    _echo("==> GitLab install complete")
    _print_access_info(cfg, domain)
    if gl.effective_tls and gl.effective_tls_mode == "self_signed":
        _print_ca_cert()
    if gl.runner and gl.runner.enabled is not False:
        _echo("")
        _echo(
            "Runner: register the in-cluster runner when GitLab is ready — "
            "`pocket --config platform.yaml gitlab register-runner` "
            "(legacy token from Rails, or `--token` / GITLAB_PERSONAL_ACCESS_TOKEN for API)."
        )


def uninstall(cfg: PlatformConfig, *, best_effort: bool = False) -> None:
    """Remove GitLab and ingress controller from the cluster.

    If ``best_effort`` is True (e.g. before ``terraform destroy``), failures to
    refresh kubeconfig or uninstall a release are logged and do not exit the process.
    """
    aws   = cfg.kubernetes.aws
    eks   = aws.eks if aws else None
    region       = aws.region if aws else "eu-central-1"
    cluster_name = eks.cluster_name if eks else "pocket-eks"

    kube = subprocess.run(
        ["aws", "eks", "update-kubeconfig",
         "--region", region, "--name", cluster_name],
        capture_output=True,
        text=True,
    )
    if kube.returncode != 0:
        err = (kube.stderr or kube.stdout or "").strip()
        if best_effort:
            _echo(
                "⚠  Skipping Helm uninstall (kubeconfig not updated). "
                + (err or f"exit {kube.returncode}"),
                error=False,
            )
            return
        if err:
            _echo(err, error=True)
        sys.exit(kube.returncode)

    for release, ns in [
        ("gitlab",        GITLAB_NAMESPACE),
        ("ingress-nginx", INGRESS_NAMESPACE),
        ("cert-manager",  CERT_MANAGER_NS),
    ]:
        _echo(f"==> Uninstalling {release}")
        result = subprocess.run(
            ["helm", "uninstall", release, "-n", ns],
            capture_output=True, text=True,
        )
        if result.returncode != 0 and "not found" not in (result.stderr or ""):
            msg = (result.stderr or result.stdout or "").strip()
            if msg:
                _echo(msg)


def get_url(cfg: PlatformConfig) -> str:
    """Return the GitLab URL (discovers NLB hostname if no custom hostname set)."""
    gl = cfg.platform.gitlab
    if gl and gl.hostname:
        scheme = "https" if gl.effective_tls else "http"
        return f"{scheme}://{gl.hostname}"

    # Discover from the ingress service (NLB hostname or, rarely, IP)
    nlb = _get_load_balancer_address(INGRESS_NAMESPACE, "ingress-nginx-controller")
    if not nlb:
        return "(NLB address not yet assigned — try again in a minute)"
    return f"http://{nlb}"


def register_runner(
    cfg: PlatformConfig,
    *,
    personal_access_token: str | None = None,
    insecure_tls: bool = False,
) -> None:
    """Register the in-cluster GitLab Runner: update the runner Secret and restart the Deployment.

    **Path 1 (legacy):** reads the instance registration token from Rails via the toolbox pod
    and patches ``runner-registration-token`` (works when Admin allows registration tokens).

    **Path 2 (modern):** with ``personal_access_token`` (or env ``GITLAB_PERSONAL_ACCESS_TOKEN``),
    calls ``POST /api/v4/runners`` and patches ``runner-token`` (``glrt-…``).
    """
    gl = cfg.platform.gitlab
    if not gl or not gl.enabled or gl.install_mode != "helm":
        _echo("✗ GitLab must be enabled with install_mode: helm.", error=True)
        sys.exit(1)
    if gl.runner and gl.runner.enabled is False:
        _echo("✗ Runner is disabled in platform.yaml.", error=True)
        sys.exit(1)

    pat = (personal_access_token or os.environ.get("GITLAB_PERSONAL_ACCESS_TOKEN") or "").strip()

    _sync_kubeconfig_from_cfg(cfg)
    tb = _toolbox_deploy_name()
    _echo(f"==> Waiting for toolbox deployment/{tb}")
    st = subprocess.run(
        ["kubectl", "rollout", "status", f"deployment/{tb}", "-n", GITLAB_NAMESPACE, "--timeout=300s"],
        capture_output=True,
        text=True,
    )
    if st.returncode != 0:
        err = (st.stderr or st.stdout or "").strip()
        _echo(f"✗ Toolbox not ready: {err or st.returncode}", error=True)
        sys.exit(1)

    # --- Path 1: legacy registration token from Rails ---
    reg = _rails_legacy_registration_token(tb)
    if reg:
        _echo("==> Using legacy instance registration token from GitLab Rails")
        _patch_runner_secret({"runner-registration-token": reg})
        _restart_gitlab_runner_deployment()
        _echo("✓ Runner secret updated (runner-registration-token). Check Admin → CI/CD → Runners.")
        return

    # --- Path 2: API with PAT ---
    if not pat:
        _echo(
            "✗ No legacy registration token (often disabled on GitLab 17+). "
            "Create a Personal Access Token with **api** scope, then:",
            error=True,
        )
        _echo(
            "    pocket --config platform.yaml gitlab register-runner --token glpat-…\n"
            "    # or: export GITLAB_PERSONAL_ACCESS_TOKEN=glpat-…",
            error=True,
        )
        _echo(
            "Admin → Settings → CI/CD → Runner registration may need enabling for the legacy path.",
            error=True,
        )
        sys.exit(1)

    base = get_url(cfg)
    if not (base.startswith("http://") or base.startswith("https://")):
        _echo(f"✗ GitLab URL not usable for API: {base}", error=True)
        sys.exit(1)

    _echo("==> Creating instance runner via GitLab API (authentication token)")
    try:
        auth_token = _api_create_instance_runner_token(base, pat, insecure=insecure_tls)
    except Exception as exc:
        _echo(f"✗ API error: {exc}", error=True)
        sys.exit(1)

    _patch_runner_secret({"runner-token": auth_token})
    _restart_gitlab_runner_deployment()
    _echo("✓ Runner secret updated (runner-token). Check Admin → CI/CD → Runners.")


# ---------------------------------------------------------------------------
# Helm values builders
# ---------------------------------------------------------------------------

def _build_runner_values(gl: Any) -> dict[str, Any]:
    """Build the gitlab-runner subchart values block."""
    runner = gl.runner if gl and gl.runner else None

    # Respect explicit enabled=false; default to enabled
    if runner and runner.enabled is False:
        return {"install": False}

    concurrent = (runner.concurrent if runner and runner.concurrent else 4)

    # Per-job resource requests/limits — sensible defaults for t3.large nodes
    cpu_req  = runner.job_cpu_request    if runner and runner.job_cpu_request    else "100m"
    mem_req  = runner.job_memory_request if runner and runner.job_memory_request else "128Mi"
    cpu_lim  = runner.job_cpu_limit      if runner and runner.job_cpu_limit      else "500m"
    mem_lim  = runner.job_memory_limit   if runner and runner.job_memory_limit   else "512Mi"

    runner_config = (
        "[[runners]]\n"
        "  [runners.kubernetes]\n"
        f'    namespace = "{GITLAB_NAMESPACE}"\n'
        '    image = "ubuntu:22.04"\n'
        f'    cpu_request = "{cpu_req}"\n'
        f'    memory_request = "{mem_req}"\n'
        f'    cpu_limit = "{cpu_lim}"\n'
        f'    memory_limit = "{mem_lim}"\n'
    )

    # `secret: nonempty` is required so the GitLab umbrella chart renders the runner Secret
    # template. `locked: null` matches the new runner auth workflow (GitLab 16+).
    return {
        "install": True,
        "concurrent": concurrent,
        "runners": {
            "secret": "nonempty",
            "executor": "kubernetes",
            "locked": None,
            "tags": "",
            "config": runner_config,
        },
    }


def _build_helm_values(cfg: PlatformConfig, domain: str) -> dict[str, Any]:
    gl      = cfg.platform.gitlab
    use_tls = gl.effective_tls if gl else False

    ingress_class = "nginx"
    if cfg.platform.ingress and cfg.platform.ingress.ingress_class:
        ingress_class = cfg.platform.ingress.ingress_class

    # When no custom hostname is set, use the bare NLB hostname directly
    # so the URL is http://<nlb-host> rather than http://gitlab.<nlb-host>
    # (AWS NLB hostnames don't support wildcard subdomain DNS resolution)
    using_bare_hostname = not (cfg.platform.gitlab and cfg.platform.gitlab.hostname)

    hosts_config: dict[str, Any] = {"https": use_tls}
    if using_bare_hostname:
        hosts_config["gitlab"] = {"name": domain}
        hosts_config["minio"]  = {"name": f"minio.{domain}"}
        hosts_config["kas"]    = {"name": f"kas.{domain}"}
    else:
        hosts_config["domain"] = domain

    values: dict[str, Any] = {
        "global": {
            "hosts": hosts_config,
            "ingress": {
                "class":                  ingress_class,
                "configureCertmanager":   use_tls,
                "tls": {"enabled": use_tls},
            },
        },
        # disable bundled nginx — we manage our own
        "nginx-ingress": {"enabled": False},
        "certmanager-issuer": {"email": LETSENCRYPT_EMAIL},
        "registry": {"enabled": False},
        "gitlab-runner": _build_runner_values(gl),
        # use smaller resource requests for non-prod
        "gitlab": {
            "webservice": {"minReplicas": 1, "maxReplicas": 2},
            "sidekiq":    {"minReplicas": 1, "maxReplicas": 1},
        },
        "redis":    {"master": {"persistence": {"size": "5Gi"}}},
        "minio":    {"persistence": {"size": "10Gi"}},
        "postgresql": {"primary": {"persistence": {"size": "8Gi"}}},
    }

    if use_tls:
        tls_mode = gl.effective_tls_mode if gl else "letsencrypt"
        issuer = SELFSIGNED_CA_ISSUER if tls_mode == "self_signed" else LETSENCRYPT_ISSUER
        values["global"]["ingress"]["annotations"] = {
            "cert-manager.io/cluster-issuer": issuer
        }

    return values


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _sync_kubeconfig_from_cfg(cfg: PlatformConfig) -> None:
    aws = cfg.kubernetes.aws
    eks = aws.eks if aws else None
    region = aws.region if aws else "eu-central-1"
    name = eks.cluster_name if eks else "pocket-eks"
    cmd = ["aws", "eks", "update-kubeconfig", "--region", region, "--name", name]
    if aws and aws.profile:
        cmd += ["--profile", aws.profile]
    _run(cmd)


def _toolbox_deploy_name() -> str:
    r = subprocess.run(
        [
            "kubectl",
            "get",
            "deploy",
            "-n",
            GITLAB_NAMESPACE,
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
        ],
        capture_output=True,
        text=True,
    )
    for name in (r.stdout or "").strip().splitlines():
        if "toolbox" in name.lower():
            return name
    return "gitlab-toolbox"


def _rails_legacy_registration_token(toolbox_deploy: str) -> str:
    """Return non-empty legacy registration token, or empty string."""
    ruby = "puts ApplicationSetting.current.runners_registration_token.to_s.strip"
    proc = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            GITLAB_NAMESPACE,
            f"deployment/{toolbox_deploy}",
            "--",
            "gitlab-rails",
            "runner",
            ruby,
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        if err:
            _echo(f"⚠  Could not read registration token from Rails: {err}", error=False)
        return ""
    return (proc.stdout or "").strip()


def _api_create_instance_runner_token(gitlab_base_url: str, pat: str, *, insecure: bool) -> str:
    url = gitlab_base_url.rstrip("/") + "/api/v4/runners"
    body = urllib.parse.urlencode(
        {
            "runner_type": "instance_type",
            "description": "pocket-eks-kubernetes",
        }
    ).encode()
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("PRIVATE-TOKEN", pat)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    ctx: ssl.SSLContext | None = None
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {detail or e.reason}") from e
    token = (payload.get("token") or "").strip()
    if not token:
        raise RuntimeError(f"API response missing token: {payload!r}")
    return token


def _patch_runner_secret(string_data: dict[str, str]) -> None:
    patch = {"stringData": string_data}
    proc = subprocess.run(
        [
            "kubectl",
            "patch",
            "secret",
            RUNNER_SECRET_NAME,
            "-n",
            GITLAB_NAMESPACE,
            "-p",
            json.dumps(patch),
            "--type=merge",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        _echo(f"✗ kubectl patch secret failed: {err or proc.returncode}", error=True)
        sys.exit(1)


def _restart_gitlab_runner_deployment() -> None:
    r = subprocess.run(
        [
            "kubectl",
            "get",
            "deploy",
            "-n",
            GITLAB_NAMESPACE,
            "-o",
            'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
        ],
        capture_output=True,
        text=True,
    )
    names = [n for n in (r.stdout or "").strip().splitlines() if n]
    target = ""
    for name in names:
        if "gitlab-runner" in name and "cache" not in name.lower():
            target = name
            break
    if not target:
        for name in names:
            if name.endswith("gitlab-runner") or name == f"{GITLAB_HELM_RELEASE}-gitlab-runner":
                target = name
                break
    if not target:
        target = f"{GITLAB_HELM_RELEASE}-gitlab-runner"
    _echo(f"==> Restarting deployment/{target}")
    subprocess.run(
        ["kubectl", "rollout", "restart", f"deployment/{target}", "-n", GITLAB_NAMESPACE],
        check=True,
    )
    subprocess.run(
        ["kubectl", "rollout", "status", f"deployment/{target}", "-n", GITLAB_NAMESPACE, "--timeout=300s"],
        check=False,
    )


def _install_cert_manager() -> None:
    _echo("==> Installing cert-manager")
    _helm_upgrade(
        release="cert-manager",
        chart="cert-manager",
        repo=CERT_MANAGER_REPO,
        namespace=CERT_MANAGER_NS,
        extra_sets={"installCRDs": "true"},
        version=CERT_MANAGER_VERSION,
    )
    _echo("==> Waiting 30s for cert-manager webhooks to be ready…")
    time.sleep(30)

    issuer_manifest = {
        "apiVersion": "cert-manager.io/v1",
        "kind": "ClusterIssuer",
        "metadata": {"name": "letsencrypt-prod"},
        "spec": {
            "acme": {
                "server": "https://acme-v02.api.letsencrypt.org/directory",
                "email": LETSENCRYPT_EMAIL,
                "privateKeySecretRef": {"name": "letsencrypt-prod"},
                "solvers": [{"http01": {"ingress": {"class": "nginx"}}}],
            }
        },
    }
    _kubectl_apply_dict(issuer_manifest)


def _helm_upgrade(
    release: str,
    chart: str,
    repo: str,
    namespace: str,
    extra_sets: dict[str, str] | None = None,
    values_dict: dict | None = None,
    version: str | None = None,
    timeout: str = "300s",
    wait: bool = True,
) -> None:
    cmd = [
        "helm", "upgrade", "--install", release, chart,
        "--repo", repo,
        "--namespace", namespace, "--create-namespace",
    ]
    if wait:
        cmd += ["--wait", "--timeout", timeout]
    if version:
        cmd += ["--version", version]
    for k, v in (extra_sets or {}).items():
        cmd += ["--set", f"{k}={v}"]
    if values_dict:
        import tempfile, os
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            yaml.dump(values_dict, f)
            tmp = f.name
        cmd += ["-f", tmp]
        try:
            _run(cmd)
        finally:
            os.unlink(tmp)
        return
    _run(cmd)


def _get_load_balancer_address(namespace: str, service: str) -> str:
    """Return the AWS LB hostname or IP from ``status.loadBalancer.ingress[0]`` (if any)."""
    for jsonpath in (
        "{.status.loadBalancer.ingress[0].hostname}",
        "{.status.loadBalancer.ingress[0].ip}",
    ):
        result = subprocess.run(
            ["kubectl", "get", "svc", "-n", namespace, service, "-o", f"jsonpath={jsonpath}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and (result.stdout or "").strip():
            return result.stdout.strip()
    return ""


def _wait_for_nlb_hostname(namespace: str, service: str, timeout: int = 600) -> str:
    deadline = time.time() + timeout
    while time.time() < deadline:
        addr = _get_load_balancer_address(namespace, service)
        if addr:
            return addr
        time.sleep(10)
    _echo("✗ Timed out waiting for NLB hostname or IP.", error=True)
    _echo(
        "Check: kubectl get svc -n "
        + f"{namespace} {service} -o wide"
        + "\n      kubectl describe svc -n "
        + f"{namespace} {service}   # Events / pending load balancer"
    )
    sys.exit(1)


def _kubectl_apply_dict(manifest: dict) -> None:
    import tempfile, os
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".yaml", delete=False
    ) as f:
        yaml.dump(manifest, f)
        tmp = f.name
    try:
        _run(["kubectl", "apply", "-f", tmp])
    finally:
        os.unlink(tmp)


def _print_access_info(cfg: PlatformConfig, domain: str) -> None:
    gl      = cfg.platform.gitlab
    use_tls = gl.effective_tls if gl else False
    scheme  = "https" if use_tls else "http"
    url     = f"{scheme}://{domain}"

    _echo("")
    _echo("=== GitLab access ===")
    _echo(f"URL:      {url}")
    _echo("User:     root")

    pwd = _wait_gitlab_initial_password(timeout=300)
    if not pwd:
        _echo(
            "Password: (Kubernetes secret not ready yet — GitLab may still be creating it.) "
            "Retry:"
        )
        _echo(
            "  kubectl get secret -n gitlab gitlab-gitlab-initial-root-password "
            "-o jsonpath='{.data.password}' | base64 -d"
        )
        if gl and gl.hostname and not use_tls:
            _echo(f"\nNote: CNAME {gl.hostname} → {domain} in your DNS provider")
        return

    vtok = read_bootstrap_root_token()
    if vtok and _write_gitlab_credentials_to_vault(url, pwd, vtok):
        _echo(
            "Password: stored in Vault KV v2 at path `secret/gitlab` "
            "(keys: `root_password`, `url`)."
        )
        _echo("          Read: pocket vault port-forward  →  then in another shell:")
        _echo(
            '            eval "$(pocket vault token --export)" && export VAULT_ADDR=http://127.0.0.1:8200 '
            "&& vault kv get secret/gitlab"
        )
    else:
        if not vtok:
            _echo(
                "Password: Vault bootstrap token not found "
                "(run pocket vault init after Vault is up). Retrieve from Kubernetes:"
            )
        else:
            _echo("Password: could not write to Vault — retrieve from Kubernetes:")
        _echo(
            "  kubectl get secret -n gitlab gitlab-gitlab-initial-root-password "
            "-o jsonpath='{.data.password}' | base64 -d"
        )

    if gl and gl.hostname and not use_tls:
        _echo(f"\nNote: CNAME {gl.hostname} → {domain} in your DNS provider")


def _install_selfsigned_issuer() -> None:
    """Create a self-signed CA chain in cert-manager (bootstrap → CA cert → CA issuer)."""
    _echo("==> Creating self-signed CA issuer")

    # 1. Bootstrap self-signed issuer (signs only the CA cert itself)
    _kubectl_apply_dict({
        "apiVersion": "cert-manager.io/v1",
        "kind": "ClusterIssuer",
        "metadata": {"name": "selfsigned-bootstrap"},
        "spec": {"selfSigned": {}},
    })

    # 2. CA certificate — signed by the bootstrap issuer
    _kubectl_apply_dict({
        "apiVersion": "cert-manager.io/v1",
        "kind": "Certificate",
        "metadata": {"name": "pocket-ca", "namespace": CERT_MANAGER_NS},
        "spec": {
            "isCA": True,
            "commonName": "pocket-ca",
            "secretName": SELFSIGNED_CA_SECRET,
            "privateKey": {"algorithm": "ECDSA", "size": 256},
            "issuerRef": {
                "name": "selfsigned-bootstrap",
                "kind": "ClusterIssuer",
                "group": "cert-manager.io",
            },
        },
    })

    _echo("==> Waiting 15s for CA certificate to be issued…")
    time.sleep(15)

    # 3. CA issuer — issues all GitLab certs using the CA above
    _kubectl_apply_dict({
        "apiVersion": "cert-manager.io/v1",
        "kind": "ClusterIssuer",
        "metadata": {"name": SELFSIGNED_CA_ISSUER},
        "spec": {"ca": {"secretName": SELFSIGNED_CA_SECRET}},
    })


def _wait_gitlab_initial_password(timeout: int = 300) -> str:
    """Poll until GitLab exposes the initial root password secret (Helm release name `gitlab`)."""
    secret_name = "gitlab-gitlab-initial-root-password"
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = subprocess.run(
            [
                "kubectl",
                "get",
                "secret",
                "-n",
                GITLAB_NAMESPACE,
                secret_name,
                "-o",
                "jsonpath={.data.password}",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0 and (result.stdout or "").strip():
            try:
                return base64.b64decode(result.stdout.strip()).decode()
            except (ValueError, UnicodeDecodeError):
                pass
        time.sleep(5)
    return ""


def _write_gitlab_credentials_to_vault(gitlab_url: str, root_password: str, vault_token: str) -> bool:
    """Store GitLab URL and initial root password in KV v2 at ``secret/gitlab``."""
    script = f"""set -e
export VAULT_ADDR=http://127.0.0.1:8200
export VAULT_TOKEN={shlex.quote(vault_token)}
vault kv put secret/gitlab root_password={shlex.quote(root_password)} url={shlex.quote(gitlab_url)}
"""
    result = subprocess.run(
        ["kubectl", "exec", "-n", "vault", "vault-0", "--", "sh", "-c", script],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if err:
            _echo(f"⚠  Vault write failed: {err}", error=False)
        return False
    return True


def _print_ca_cert() -> None:
    """Extract the self-signed CA cert and save it locally for browser import."""
    import pathlib

    result = subprocess.run(
        ["kubectl", "get", "secret", "-n", CERT_MANAGER_NS, SELFSIGNED_CA_SECRET,
         "-o", "jsonpath={.data.ca\\.crt}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        _echo("⚠  Could not retrieve CA certificate — try: "
              f"kubectl get secret -n {CERT_MANAGER_NS} {SELFSIGNED_CA_SECRET} "
              "-o jsonpath='{.data.ca\\.crt}' | base64 -d")
        return

    ca_pem = base64.b64decode(result.stdout.strip()).decode()
    ca_file = pathlib.Path("pocket-ca.crt")
    ca_file.write_text(ca_pem)

    _echo("\n=== Self-signed CA Certificate ===")
    _echo(f"Saved to: {ca_file.resolve()}")
    _echo("\nTo trust it system-wide:")
    _echo("  macOS:  sudo security add-trusted-cert -d -r trustRoot "
          f"-k /Library/Keychains/System.keychain {ca_file}")
    _echo("  Linux:  sudo cp pocket-ca.crt /usr/local/share/ca-certificates/ "
          "&& sudo update-ca-certificates")
    _echo("  Or import pocket-ca.crt manually in your browser's certificate settings.")


def _upsert_route53_cname(zone_id: str, hostname: str, nlb_host: str) -> None:
    """Create or update a CNAME record in Route 53 pointing hostname → nlb_host."""
    try:
        import boto3
    except ImportError:
        _echo("✗ boto3 is required for Route 53 automation. Run: pip install boto3", error=True)
        sys.exit(1)

    _echo(f"==> Upserting Route 53 CNAME: {hostname} → {nlb_host}")
    client = boto3.client("route53")
    client.change_resource_record_sets(
        HostedZoneId=zone_id,
        ChangeBatch={
            "Comment": f"pocket gitlab install — {hostname}",
            "Changes": [
                {
                    "Action": "UPSERT",
                    "ResourceRecordSet": {
                        "Name": hostname,
                        "Type": "CNAME",
                        "TTL": 60,
                        "ResourceRecords": [{"Value": nlb_host}],
                    },
                }
            ],
        },
    )
    _echo(f"    CNAME record created (TTL 60s)")


def _wait_for_dns(hostname: str, expected_cname: str, timeout: int = 300) -> None:
    """Poll until the hostname resolves to (or via) expected_cname, or timeout."""
    import socket
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            resolved = socket.getfqdn(hostname)
            # getfqdn follows the chain; check if NLB hostname appears anywhere
            if expected_cname in resolved or resolved != hostname:
                _echo(f"    DNS resolved: {hostname} → {resolved}")
                return
        except OSError:
            pass
        _echo(f"    Still waiting for DNS… (checking every 15s)")
        time.sleep(15)
    _echo("⚠  DNS did not propagate within timeout — proceeding anyway (cert-manager will retry)", error=False)


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd)
    if result.returncode != 0:
        _echo(f"✗ Command failed: {' '.join(cmd)}", error=True)
        sys.exit(result.returncode)


def _echo(msg: str, error: bool = False) -> None:
    import click
    click.echo(click.style(msg, fg="red") if error else msg)
