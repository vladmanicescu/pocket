



# Makefile Targets Documentation

This document explains the available `make` targets used to provision infrastructure, configure systems, and manage the Kubernetes environment.

The automation stack uses:

- Terraform → infrastructure provisioning
- Ansible → configuration management
- Helm → Kubernetes storage provisioning
- GitLab → CI/CD platform

---

# Directory Structure

The Makefile uses the following directories:

| Variable | Path | Description |
|--------|--------|--------|
| TERRAFORM_DIR | terraform/ | Terraform infrastructure code |
| ANSIBLE_DIR | ansible/ | Ansible playbooks and inventory |

Important files:

| Variable | File |
|--------|--------|
| INVENTORY | ansible/inventory.ini |
| CONFIG_PLAYBOOK | ansible/playbook.yml |
| NFS_PLAYBOOK | ansible/nfs.yaml |
| CLUSTER_PLAYBOOK | ansible/k8s-cluster.yml |

---

# Infrastructure Targets

## make infra

Provisions infrastructure using Terraform.

Steps executed:

1. Initializes Terraform
2. Applies the Terraform plan automatically

Commands:

cd terraform && terraform init  
cd terraform && terraform apply -auto-approve

---

## make destroy

Destroys all Terraform-managed infrastructure.

Command:

cd terraform && terraform destroy -auto-approve

---

## make fmt

Formats Terraform code according to standard conventions.

Command:

cd terraform && terraform fmt

---

## make validate

Validates Terraform configuration syntax.

Command:

cd terraform && terraform validate

---

# Configuration Targets

## make config

Configures Kubernetes nodes using Ansible.

Command:

cd ansible && ansible-playbook -i inventory.ini playbook.yml

Typical tasks include:

- Installing container runtime
- Installing Kubernetes dependencies
- Preparing nodes for cluster creation

---

## make nfs

Configures the NFS server used for shared Kubernetes storage.

Command:

cd ansible && ansible-playbook -i inventory.ini nfs.yaml

---

## make storage

Installs a Kubernetes NFS dynamic storage provisioner using Helm.

Command:

cd ansible && ansible-playbook -i inventory.ini nfs-provisioner.yaml

This creates a StorageClass that allows Kubernetes to dynamically provision volumes backed by NFS.

---

## make cluster

Creates the Kubernetes cluster.

Command:

cd ansible && ansible-playbook -i inventory.ini k8s-cluster.yml

Typical actions:

- Initialize control plane
- Join worker nodes
- Configure cluster networking

---

# GitLab Targets

## make gitlab

Installs a GitLab server using Ansible.

Command:

cd ansible && ansible-playbook -i inventory.ini gitlab.yaml

---

## make gitlab-bootstrap

Bootstraps GitLab after installation.

Command:

cd ansible && ansible-playbook -i inventory.ini gitlab-bootstrap.yaml

Typical actions:

- Create GitLab projects
- Configure API tokens
- Prepare repositories for CI/CD

---

## make push-gitlab

Pushes local repositories to GitLab using a bootstrap script.

Required variables:

- GITLAB_IP
- GITLAB_TOKEN

Usage:

make push-gitlab GITLAB_IP=<ip> GITLAB_TOKEN=<token>

Script executed:

./scripts/bootstrap_gitlab.sh <GITLAB_IP> <GITLAB_TOKEN>

---

# Combined Targets

## make config-infra

Runs both infrastructure provisioning and node configuration.

Equivalent to:

make infra  
make config

---

## make all-k8s

Runs the complete Kubernetes setup pipeline.

Equivalent to:

make infra  
make config  
make nfs  
make cluster

This target:

1. Provisions infrastructure
2. Configures nodes
3. Installs NFS server
4. Builds the Kubernetes cluster

---

# Connectivity Tests

## make test

Tests connectivity to Kubernetes nodes using Ansible ping.

Command:

cd ansible && ansible -i inventory.ini k8s -m ping

---

## make test-nfs

Tests connectivity to the NFS host.

Command:

cd ansible && ansible -i inventory.ini nfs -m ping

---

## make test-gitlab

Tests connectivity to the GitLab server.

Command:

cd ansible && ansible -i inventory.ini gitlab -m ping

---

# Typical Workflow

A typical environment setup would be:

make infra  
make config  
make nfs  
make cluster  
make storage  
make gitlab  
make gitlab-bootstrap

Or using the combined target:

make all-k8s

---

# Summary

| Target | Purpose |
|------|------|
| infra | Provision infrastructure |
| config | Configure Kubernetes nodes |
| nfs | Install NFS server |
| storage | Install NFS StorageClass |
| cluster | Create Kubernetes cluster |
| gitlab | Install GitLab |
| gitlab-bootstrap | Configure GitLab |
| push-gitlab | Push repositories |
| test | Test Kubernetes nodes |
| test-nfs | Test NFS server |
| test-gitlab | Test GitLab |
| destroy | Destroy infrastructure |
| fmt | Format Terraform |
| validate | Validate Terraform |

