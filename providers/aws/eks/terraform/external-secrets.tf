# External Secrets Operator — syncs secrets from Vault into Kubernetes Secrets.

resource "helm_release" "external_secrets" {
  name             = "external-secrets"
  namespace        = "external-secrets"
  create_namespace = true

  repository = "https://charts.external-secrets.io"
  chart      = "external-secrets"
  version    = "0.9.20"

  wait    = true
  timeout = 600

  set {
    name  = "installCRDs"
    value = "true"
  }

  depends_on = [
    module.eks,
    kubernetes_storage_class.gp3,
  ]
}
