# HashiCorp Vault on EKS with integrated storage (Raft) and AWS KMS auto-unseal.
# IRSA grants the Vault server pod permission to use the dedicated KMS key.
# Toggle with var.vault_enabled (driven by platform.yaml → pocket → terraform.tfvars).

resource "aws_kms_key" "vault" {
  count = var.vault_enabled ? 1 : 0

  description             = "${var.cluster_name} Vault seal / auto-unseal"
  deletion_window_in_days = 7
  enable_key_rotation     = true

  tags = {
    Project = var.project_name
    Name    = "${var.cluster_name}-vault-seal"
  }
}

resource "aws_kms_alias" "vault" {
  count = var.vault_enabled ? 1 : 0

  name          = "alias/${var.cluster_name}-vault-seal"
  target_key_id = aws_kms_key.vault[0].key_id
}

data "aws_iam_policy_document" "vault_kms" {
  count = var.vault_enabled ? 1 : 0

  statement {
    sid    = "VaultKMS"
    effect = "Allow"
    actions = [
      "kms:Encrypt",
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:GenerateDataKey",
      "kms:ReEncrypt*",
    ]
    resources = [aws_kms_key.vault[0].arn]
  }
}

resource "aws_iam_policy" "vault_kms" {
  count = var.vault_enabled ? 1 : 0

  name_prefix = "${var.cluster_name}-vault-kms-"
  policy      = data.aws_iam_policy_document.vault_kms[0].json

  tags = {
    Project = var.project_name
  }
}

module "vault_irsa" {
  source  = "terraform-aws-modules/iam/aws//modules/iam-role-for-service-accounts-eks"
  version = "~> 5.0"

  count = var.vault_enabled ? 1 : 0

  role_name = "${var.cluster_name}-vault"

  role_policy_arns = {
    vault_kms = aws_iam_policy.vault_kms[0].arn
  }

  oidc_providers = {
    main = {
      provider_arn               = module.eks.oidc_provider_arn
      namespace_service_accounts = ["vault:vault"]
    }
  }

  tags = {
    Project = var.project_name
  }
}

locals {
  vault_helm_version = "0.29.1"

  vault_seal_config = var.vault_enabled ? format(
    <<-EOT
ui = true

listener "tcp" {
  tls_disable     = 1
  address         = "[::]:8200"
  cluster_address = "[::]:8201"
}

storage "raft" {
  path = "/vault/data"
}

service_registration "kubernetes" {}

seal "awskms" {
  region     = "%s"
  kms_key_id = "%s"
}
EOT
    , var.aws_region, aws_kms_alias.vault[0].name) : ""
}

resource "helm_release" "vault" {
  count = var.vault_enabled ? 1 : 0

  name             = "vault"
  repository       = "https://helm.releases.hashicorp.com"
  chart            = "vault"
  version          = local.vault_helm_version
  namespace        = "vault"
  create_namespace = true

  # yamlencode() emits valid YAML for multiline raft.config (avoids fragile .tftpl + indent).
  values = [
    yamlencode({
      injector = { enabled = false }
      server = {
        standalone = { enabled = false }
        ha = {
          enabled  = true
          replicas = var.vault_replicas
          raft = {
            enabled   = true
            setNodeId = true
            config    = trimspace(local.vault_seal_config)
          }
        }
        serviceAccount = {
          create = true
          name   = "vault"
          annotations = {
            "eks.amazonaws.com/role-arn" = module.vault_irsa[0].iam_role_arn
          }
        }
        extraEnvironmentVars = {
          AWS_REGION = var.aws_region
        }
        dataStorage = {
          enabled      = true
          size         = var.vault_data_storage_size
          storageClass = kubernetes_storage_class.gp3.metadata[0].name
        }
      }
    })
  ]

  depends_on = [
    module.eks,
    module.vault_irsa[0],
    kubernetes_storage_class.gp3,
  ]
}
