# AWS — vanilla Kubernetes

Infrastructure and configuration for a self-managed kubeadm cluster on EC2, plus NFS and GitLab VMs, live here:

- `terraform/` — VPC, EC2, key pair, generated Ansible inventory
- `ansible/` — node prep, cluster, NFS, GitLab, bootstrap

From the repository root, use `make infra`, `make config`, and the other targets; `makefile` points `TERRAFORM_DIR` and `ANSIBLE_DIR` at these paths.
