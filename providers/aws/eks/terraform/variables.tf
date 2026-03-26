variable "aws_region" {
  type        = string
  description = "AWS region for the EKS cluster and VPC"
  default     = "eu-central-1"
}

variable "aws_profile" {
  type        = string
  description = "AWS CLI profile for credentials"
  default     = "default"
}

variable "project_name" {
  type        = string
  description = "Prefix for resource names and tags"
  default     = "pocket"
}

variable "cluster_name" {
  type        = string
  description = "EKS cluster name (must be unique in the account/region)"
  default     = "pocket-eks"
}

variable "cluster_version" {
  type        = string
  description = "Kubernetes version for the control plane (EKS supported version)"
  default     = "1.31"
}

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR for the dedicated EKS VPC"
  default     = "10.40.0.0/16"
}

variable "az_count" {
  type        = number
  description = "Number of availability zones to use (2–3 recommended)"
  default     = 2
}

variable "single_nat_gateway" {
  type        = bool
  description = "Use one NAT gateway for all AZs (cheaper; fine for non-prod)"
  default     = true
}

variable "cluster_endpoint_public_access" {
  type        = bool
  description = "Expose the Kubernetes API publicly (typical for kubectl from laptop)"
  default     = true
}

variable "cluster_endpoint_private_access" {
  type        = bool
  description = "Allow API access from within the VPC"
  default     = true
}

variable "node_instance_types" {
  type        = list(string)
  description = "Instance types for the managed node group"
  default     = ["t3.medium"]
}

variable "node_desired_size" {
  type    = number
  default = 2
}

variable "node_min_size" {
  type    = number
  default = 1
}

variable "node_max_size" {
  type    = number
  default = 4
}

variable "node_disk_size" {
  type        = number
  description = "Root volume size (GiB) for managed nodes"
  default     = 50
}

variable "vault_enabled" {
  type        = bool
  description = "Provision Vault (KMS + IRSA + Helm). Set via platform.yaml / pocket."
  default     = true
}

variable "vault_replicas" {
  type        = number
  description = "Vault server replicas (Raft). Use 3+ for production HA; 1 is fine for lab."
  default     = 1
}

variable "vault_data_storage_size" {
  type        = string
  description = "PVC size for Vault Raft data (gp3)"
  default     = "10Gi"
}
