
# devops_assignment (pocket)

**pocket** is a small CLI plus Terraform layouts to provision **AWS EKS** (or vanilla EC2-based Kubernetes), optional **HashiCorp Vault**, **External Secrets**, **GitLab** (Helm), and related pieces from a single **`platform.yaml`**.

- **License:** [MIT](LICENSE)
- **Contributing:** [CONTRIBUTING.md](CONTRIBUTING.md)
- **Security:** [SECURITY.md](SECURITY.md)

EKS-focused docs: [`providers/aws/eks/README.md`](providers/aws/eks/README.md).

**pocket commands:** [`docs/pocket-cli.md`](docs/pocket-cli.md).

**Local CLI:** `make dev-setup`, then `source .venv/bin/activate` — **`pocket`** is on your PATH like any other tool. Details: [`docs/pocket-cli.md`](docs/pocket-cli.md).

---

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
| TERRAFORM_DIR | providers/aws/vanilla/terraform/ | Terraform (AWS vanilla k8s on EC2) |
| EKS_TERRAFORM_DIR | providers/aws/eks/terraform/ | Terraform (managed EKS + VPC) |
| ANSIBLE_DIR | providers/aws/vanilla/ansible/ | Ansible playbooks and inventory |

Important files:

| Variable | File |
|--------|--------|
| INVENTORY | providers/aws/vanilla/ansible/inventory.ini |
| CONFIG_PLAYBOOK | providers/aws/vanilla/ansible/playbook.yml |
| NFS_PLAYBOOK | providers/aws/vanilla/ansible/nfs.yaml |
| CLUSTER_PLAYBOOK | providers/aws/vanilla/ansible/k8s-cluster.yml |

---

# Infrastructure Targets

## make infra

Provisions infrastructure using Terraform.

Steps executed:

1. Initializes Terraform
2. Applies the Terraform plan automatically

Commands:

cd providers/aws/vanilla/terraform && terraform init  
cd providers/aws/vanilla/terraform && terraform apply -auto-approve

---

## make infra-eks

Provisions **Amazon EKS** (managed Kubernetes) in a dedicated VPC. Code lives under `providers/aws/eks/terraform` (see `providers/aws/eks/README.md`). After apply, run `terraform output configure_kubeconfig` from that directory and execute the printed `aws eks update-kubeconfig` command.

Commands:

cd providers/aws/eks/terraform && terraform init  
cd providers/aws/eks/terraform && terraform apply -auto-approve

---

## make destroy

Destroys all Terraform-managed infrastructure.

Command:

cd providers/aws/vanilla/terraform && terraform destroy -auto-approve

---

## make destroy-eks

Destroys the EKS stack (same directory as `make infra-eks`).

Command:

cd providers/aws/eks/terraform && terraform destroy -auto-approve

---

## make fmt

Formats Terraform code according to standard conventions.

Command:

cd providers/aws/vanilla/terraform && terraform fmt

---

## make validate

Validates Terraform configuration syntax.

Command:

cd providers/aws/vanilla/terraform && terraform validate

---

# Configuration Targets

## make config

Configures Kubernetes nodes using Ansible.

Command:

cd providers/aws/vanilla/ansible && ansible-playbook -i inventory.ini playbook.yml

Typical tasks include:

- Installing container runtime
- Installing Kubernetes dependencies
- Preparing nodes for cluster creation

---

## make nfs

Configures the NFS server used for shared Kubernetes storage.

Command:

cd providers/aws/vanilla/ansible && ansible-playbook -i inventory.ini nfs.yaml

---

## make storage

Installs a Kubernetes NFS dynamic storage provisioner using Helm.

Command:

cd providers/aws/vanilla/ansible && ansible-playbook -i inventory.ini nfs-provisioner.yaml

This creates a StorageClass that allows Kubernetes to dynamically provision volumes backed by NFS.

---

## make cluster

Creates the Kubernetes cluster.

Command:

cd providers/aws/vanilla/ansible && ansible-playbook -i inventory.ini k8s-cluster.yml

Typical actions:

- Initialize control plane
- Join worker nodes
- Configure cluster networking

---

# GitLab Targets

## make gitlab

Installs a GitLab server using Ansible.

Command:

cd providers/aws/vanilla/ansible && ansible-playbook -i inventory.ini gitlab.yaml

---

## make gitlab-bootstrap

Bootstraps GitLab after installation.

Command:

cd providers/aws/vanilla/ansible && ansible-playbook -i inventory.ini gitlab-bootstrap.yaml

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

cd providers/aws/vanilla/ansible && ansible -i inventory.ini k8s -m ping

---

## make test-nfs

Tests connectivity to the NFS host.

Command:

cd providers/aws/vanilla/ansible && ansible -i inventory.ini nfs -m ping

---

## make test-gitlab

Tests connectivity to the GitLab server.

Command:

cd providers/aws/vanilla/ansible && ansible -i inventory.ini gitlab -m ping

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
| infra-eks | Provision EKS (`providers/aws/eks/terraform`) |
| destroy-eks | Destroy EKS stack |
| fmt-eks | Format EKS Terraform |
| validate-eks | Validate EKS Terraform |

---

# Infrastructure Provisioning

```bash
cd providers/aws/vanilla/terraform
terraform init
terraform plan
terraform apply
```

Important outputs:

- Kubernetes control plane IP
- GitLab server IP
- SSH private key

---

# Download kubeconfig



```bash
scp ec2-user@<control-plane-ip>:/etc/kubernetes/admin.conf ./kubeconfig
export KUBECONFIG=$(pwd)/kubeconfig
```

Verify:

```bash
kubectl get nodes
kubectl get pods -A
```

---

# Install NFS Storage

```bash
helm repo add nfs-subdir-external-provisioner https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/
helm repo update
```

Install:

```bash
helm install nfs-client \
nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
--namespace nfs-provisioner \
--create-namespace
```

Verify:

```bash
kubectl get pods -n nfs-provisioner
kubectl get storageclass
```

---

# Install GitLab

```bash
cd providers/aws/vanilla/ansible
ansible-playbook -i inventory.ini gitlab.yaml
```

Verify services:

```bash
sudo gitlab-ctl status
sudo gitlab-ctl tail
```

Open:

```
http://<gitlab-ip>
```

---

# Fix GitLab Root Login

```bash
sudo gitlab-rake "gitlab:password:reset[root]"
```

Verify:

```bash
sudo gitlab-rails runner "puts User.pluck(:username)"
```

---

# Bootstrap GitLab

```bash
cd providers/aws/vanilla/ansible
ansible-playbook -i inventory.ini gitlab-bootstrap.yaml
```

Projects created:

- python-auth-app
- python-auth-k8s

---

# Install GitLab Runner

```bash
sudo dnf install gitlab-runner -y
sudo systemctl enable --now gitlab-runner
```

Register:

```bash
sudo gitlab-runner register \
--url http://<gitlab-ip> \
--executor shell \
--token <RUNNER_TOKEN>
```

Verify:

```bash
sudo gitlab-runner verify
```

---

# Install Docker on GitLab Server

```bash
sudo dnf install docker -y
sudo systemctl enable --now docker
sudo usermod -aG docker gitlab-runner
sudo systemctl restart gitlab-runner
```

Verify:

```bash
docker info
```

---

# Configure Docker Insecure Registry

Edit:

```
/etc/docker/daemon.json
```

```json
{
"insecure-registries": ["<gitlab-ip>:5050"]
}
```

Restart Docker:

```bash
sudo systemctl restart docker
```

---

# Test Registry Login

```bash
docker login http://<gitlab-ip>:5050
```

---

# Fix CI Job Token Error

```bash
sudo gitlab-rails runner 'key = OpenSSL::PKey::RSA.new(2048).to_pem; s = ApplicationSetting.current; s.ci_job_token_signing_key = key; s.save!'
```

Restart GitLab:

```bash
sudo gitlab-ctl restart
```

---

# GitLab CI Pipeline

```yaml
stages:
- build

build:
stage: build
script:
- docker login http://<gitlab-ip>:5050 -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD
- docker build -t <gitlab-ip>:5050/root/python-auth-app:$CI_COMMIT_SHORT_SHA .
- docker push <gitlab-ip>:5050/root/python-auth-app:$CI_COMMIT_SHORT_SHA
```

---

# Deploy with Helm

```bash
helm install auth-app app/python-auth-k8s
```

Verify:

```bash
kubectl get pods
kubectl get svc
```

---

Push repositories:

```bash
make push-gitlab GITLAB_IP=<gitlab-ip> GITLAB_TOKEN=<token>
```

---

# CI/CD Flow

```
git push
   ↓
GitLab CI
   ↓
Docker build
   ↓
GitLab Container Registry
   ↓
Helm deployment
   ↓
Kubernetes
```
"""