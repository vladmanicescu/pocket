# pocket CLI — bin/pocket bootstraps .venv if needed; interactive use: source .venv/bin/activate && pocket …
POCKET ?= bin/pocket

TERRAFORM_DIR=providers/aws/vanilla/terraform
ANSIBLE_DIR=providers/aws/vanilla/ansible
EKS_TERRAFORM_DIR=providers/aws/eks/terraform

INVENTORY=$(ANSIBLE_DIR)/inventory.ini
CONFIG_PLAYBOOK=$(ANSIBLE_DIR)/playbook.yml
NFS_PLAYBOOK=$(ANSIBLE_DIR)/nfs.yaml
CLUSTER_PLAYBOOK=$(ANSIBLE_DIR)/k8s-cluster.yml

.PHONY: dev-setup infra config nfs cluster config-infra all-k8s test test-nfs destroy fmt validate infra-eks destroy-eks fmt-eks validate-eks

dev-setup:
	@echo "==> Ensuring virtualenv and installing pocket"
	@if [ -f .venv/bin/python ]; then \
		echo "    (existing .venv)"; \
	else \
		echo "    (creating .venv)"; \
		if [ -d .venv ]; then rm -rf .venv; fi; \
		uv venv; \
	fi
	uv pip install -e ".[dev]"
	@PURELIB=$$(.venv/bin/python -c "import sysconfig; print(sysconfig.get_path('purelib'))") && \
		echo "$(CURDIR)/src" > "$$PURELIB/pocket-path.pth"
	@cp "$(CURDIR)/scripts/pocket-venv-launcher.sh" "$(CURDIR)/.venv/bin/pocket"
	@chmod +x "$(CURDIR)/.venv/bin/pocket"
	@echo "==> Done. Run: source .venv/bin/activate   then: pocket <command>"

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
	cd $(ANSIBLE_DIR) && ansible-playbook -i inventory.ini gitlab.yaml

gitlab-bootstrap:
	@echo "==> Bootstrapping GitLab projects and token"
	cd $(ANSIBLE_DIR) && ansible-playbook -i inventory.ini gitlab-bootstrap.yaml

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
	cd $(ANSIBLE_DIR) && ansible -i inventory.ini gitlab -m ping

destroy:
	@echo "==> Destroying infrastructure"
	cd $(TERRAFORM_DIR) && terraform destroy -auto-approve

fmt:
	@echo "==> Formatting Terraform code"
	cd $(TERRAFORM_DIR) && terraform fmt

validate:
	@echo "==> Validating Terraform configuration"
	cd $(TERRAFORM_DIR) && terraform validate

infra-eks:
	@echo "==> Writing terraform.tfvars from platform.yaml (EKS + Vault + node settings)"
	@test -x $(POCKET) || (echo "Run: make dev-setup  (then use $(POCKET) or activate .venv)" && exit 1)
	$(POCKET) apply --config platform.yaml
	@echo "==> Terraform apply: EKS cluster, VPC, EBS CSI, gp3, Vault (if platform.vault.enabled)"
	cd $(EKS_TERRAFORM_DIR) && terraform init
	cd $(EKS_TERRAFORM_DIR) && terraform apply -auto-approve

destroy-eks:
	@echo "==> Destroy EKS stack (writes tfvars, Helm uninstall if GitLab/helm, then terraform destroy)"
	@test -x $(POCKET) || (echo "Run: make dev-setup" && exit 1)
	$(POCKET) destroy --config platform.yaml --yes

fmt-eks:
	@echo "==> Formatting EKS Terraform"
	cd $(EKS_TERRAFORM_DIR) && terraform fmt -recursive

validate-eks:
	@echo "==> Validating EKS Terraform"
	cd $(EKS_TERRAFORM_DIR) && terraform validate