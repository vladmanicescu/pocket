variable "aws_region" {
  description = "AWS region where resources will be created"
  type        = string
  default     = "eu-central-1"
}

variable "aws_profile" {
  description = "AWS CLI profile used by Terraform"
  type        = string
  default     = "default"
}

variable "project_name" {
  description = "Project name prefix used for tagging"
  type        = string
  default     = "pocket"
}

variable "key_name" {
  description = "Name of the generated AWS key pair"
  type        = string
  default     = "k8s-pocket-key"
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "172.31.0.0/16"
}

variable "subnet_cidr" {
  description = "CIDR block for the public subnet"
  type        = string
  default     = "172.31.1.0/24"
}

variable "availability_zone" {
  description = "Availability zone for the subnet and EC2 instances"
  type        = string
  default     = "eu-central-1a"
}

variable "allowed_ssh_cidrs" {
  description = "CIDR blocks allowed to access SSH, GitLab and Kubernetes API"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

variable "instance_type" {
  description = "EC2 instance type used for Kubernetes nodes and NFS server"
  type        = string
  default     = "t3.medium"
}

variable "root_volume_size" {
  description = "Root disk size in GiB for Kubernetes nodes"
  type        = number
  default     = 20
}

variable "vm_definitions" {
  description = "Definitions for Kubernetes VMs and their extra data disks"
  type = list(object({
    name            = string
    hostname        = string
    private_ip      = string
    gateway         = optional(string)
    extra_disk_size = number
  }))

  default = [
    {
      name            = "k8s-cp1"
      hostname        = "k8s-cp1"
      private_ip      = "172.31.1.11"
      gateway         = "172.31.1.1"
      extra_disk_size = 3
    },
    {
      name            = "k8s-w1"
      hostname        = "k8s-w1"
      private_ip      = "172.31.1.12"
      gateway         = "172.31.1.1"
      extra_disk_size = 3
    },
    {
      name            = "k8s-w2"
      hostname        = "k8s-w2"
      private_ip      = "172.31.1.13"
      gateway         = "172.31.1.1"
      extra_disk_size = 3
    }
  ]
}