output "cluster_name" {
  description = "EKS cluster name"
  value       = module.eks.cluster_name
}

output "cluster_endpoint" {
  description = "Kubernetes API endpoint"
  value       = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64 CA data for kubeconfig"
  value       = module.eks.cluster_certificate_authority_data
  sensitive   = true
}

output "cluster_oidc_issuer_url" {
  description = "OIDC issuer URL (for IRSA)"
  value       = module.eks.cluster_oidc_issuer_url
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

locals {
  kubeconfig_cli_cmd = "aws eks update-kubeconfig --region ${var.aws_region} --name ${module.eks.cluster_name} --profile ${var.aws_profile}"
}

output "configure_kubeconfig" {
  description = "Shell command to merge kubeconfig for this cluster (run as-is)"
  value       = local.kubeconfig_cli_cmd
}

output "configure_kubectl" {
  description = "Same as configure_kubeconfig (alias)"
  value       = local.kubeconfig_cli_cmd
}

output "vault_kms_key_arn" {
  description = "KMS key used for Vault auto-unseal (awskms seal)"
  value       = var.vault_enabled ? aws_kms_key.vault[0].arn : null
}

output "vault_kms_alias" {
  description = "KMS alias for the Vault seal key"
  value       = var.vault_enabled ? aws_kms_alias.vault[0].name : null
}

output "vault_irsa_role_arn" {
  description = "IAM role ARN assumed by the Vault server service account (IRSA)"
  value       = var.vault_enabled ? module.vault_irsa[0].iam_role_arn : null
}

output "vault_init_hint" {
  description = "One-time cluster initialization (run after pods are Ready)"
  value = var.vault_enabled ? join("\n", [
    "pocket vault init   # stores root token + recovery keys in Secret vault/pocket-vault-bootstrap",
    "pocket vault token  # or: pocket vault token --export",
    "pocket vault bootstrap   # uses that Secret if VAULT_TOKEN is unset",
    "pocket vault status",
  ]) : "Vault is disabled (vault_enabled = false)."
}
