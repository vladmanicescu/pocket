TERRAFORM_DIR=terraform
ANSIBLE_DIR=ansible

INVENTORY=$(ANSIBLE_DIR)/inventory.ini
CONFIG_PLAYBOOK=$(ANSIBLE_DIR)/playbook.yml
NFS_PLAYBOOK=$(ANSIBLE_DIR)/nfs.yaml
CLUSTER_PLAYBOOK=$(ANSIBLE_DIR)/k8s-cluster.yml

.PHONY: infra config nfs cluster config-infra all-k8s test test-nfs destroy fmt validate

infra:
	@echo "==> Provisioning infrastructure with Terraform"
	cd $(TERRAFORM_DIR) && terraform init
	cd $(TERRAFORM_DIR) && terraform apply -auto-approve

config:
	@echo "==> Configuring Kubernetes nodes with Ansible"
	cd $(ANSIBLE_DIR) && ansible-playbook -i inventory.ini playbook.yml

nfs:
	@echo "==> Configuring NFS server with Ansible"
	cd $(ANSIBLE_DIR) && ansible-playbook -i inventory.ini nfs.yaml

storage:
	@echo "==> Installing NFS StorageClass via Helm"
	cd $(ANSIBLE_DIR) && ansible-playbook -i inventory.ini nfs-provisioner.yaml

gitlab:
	@echo "==> Installing GitLab server"
	cd ansible && ansible-playbook -i inventory.ini gitlab.yaml

gitlab-bootstrap:
	@echo "==> Bootstrapping GitLab projects and token"
	cd ansible && ansible-playbook -i inventory.ini gitlab-bootstrap.yaml

push-gitlab:
	@echo "Usage: make push-gitlab GITLAB_IP=<ip> GITLAB_TOKEN=<token>"
	@test -n "$(GITLAB_IP)" && test -n "$(GITLAB_TOKEN)"
	./scripts/bootstrap_gitlab.sh $(GITLAB_IP) $(GITLAB_TOKEN)

cluster:
	@echo "==> Building Kubernetes cluster"
	cd $(ANSIBLE_DIR) && ansible-playbook -i inventory.ini k8s-cluster.yml

config-infra: infra config

all-k8s: infra config nfs cluster

test:
	@echo "==> Testing Kubernetes nodes connectivity with Ansible ping"
	cd $(ANSIBLE_DIR) && ansible -i inventory.ini k8s -m ping

test-nfs:
	@echo "==> Testing NFS host connectivity with Ansible ping"
	cd $(ANSIBLE_DIR) && ansible -i inventory.ini nfs -m ping

test-gitlab:
	@echo "==> Testing GitLab host connectivity"
	cd ansible && ansible -i inventory.ini gitlab -m ping

destroy:
	@echo "==> Destroying infrastructure"
	cd $(TERRAFORM_DIR) && terraform destroy -auto-approve

fmt:
	@echo "==> Formatting Terraform code"
	cd $(TERRAFORM_DIR) && terraform fmt

validate:
	@echo "==> Validating Terraform configuration"
	cd $(TERRAFORM_DIR) && terraform validate