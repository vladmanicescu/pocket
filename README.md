# devops_assignment
# Create the README.md file for the user to download

import pypandoc

text = r"""
# DevOps Assignment – End-to-End Setup

## Overview

Acest proiect configurează complet infrastructura și pipeline-ul DevOps:

- Terraform – infrastructură AWS
- Kubernetes – cluster pentru aplicație
- NFS dynamic storage provisioner
- GitLab self-hosted
- GitLab Runner (shell executor)
- Docker build & push
- GitLab Container Registry
- Helm chart pentru deployment
- posibil ArgoCD GitOps deployment

---

# 1. Provision Infrastructure

Initializează Terraform:

```bash
terraform init
terraform plan
terraform apply
```

Outputuri utile:

- Kubernetes control plane IP
- GitLab server IP
- SSH private key

---

# 2. Install Kubernetes Storage (NFS)

Adaugă Helm repo:

```bash
helm repo add nfs-subdir-external-provisioner https://kubernetes-sigs.github.io/nfs-subdir-external-provisioner/
helm repo update
```

## values.yaml

```yaml
nfs:
  server: <NFS_SERVER_IP>
  path: /srv/nfs/kubedata

storageClass:
  create: true
  name: nfs-client
  defaultClass: false
  reclaimPolicy: Retain
  allowVolumeExpansion: true

rbac:
  create: true
```

Install provisioner:

```bash
helm install nfs-client \
  nfs-subdir-external-provisioner/nfs-subdir-external-provisioner \
  -f values.yaml \
  --namespace nfs-provisioner \
  --create-namespace
```

Verificare:

```bash
kubectl get pods -n nfs-provisioner
kubectl get storageclass
```

---

# 3. Install GitLab Server

GitLab a fost instalat pe un EC2 folosind Ansible.

```bash
ansible-playbook -i inventory.ini gitlab.yaml
```

Verificare:

```bash
sudo gitlab-ctl status
curl http://localhost
```

---

# 4. Fix GitLab Root Access

Reset password:

```bash
sudo gitlab-rake "gitlab:password:reset[root]"
```

---

# 5. Create GitLab Projects

```bash
ansible-playbook gitlab-bootstrap.yaml
```

Proiecte create:

- python-auth-app
- python-auth-k8s

---

# 6. Push Local Repositories

```bash
make push-gitlab GITLAB_IP=3.67.91.250 GITLAB_TOKEN=<TOKEN>
```

---

# 7. Install GitLab Runner

```bash
sudo dnf install gitlab-runner -y
sudo systemctl enable --now gitlab-runner
```

Register runner:

```bash
sudo gitlab-runner register \
  --url http://3.67.91.250 \
  --executor shell \
  --token <RUNNER_TOKEN>
```

---

# 8. Install Docker

```bash
sudo dnf install docker -y
sudo systemctl enable --now docker
sudo usermod -aG docker gitlab-runner
sudo systemctl restart gitlab-runner
```

---

# 9. Configure Docker Registry

Edit:

```
/etc/docker/daemon.json
```

```json
{
  "insecure-registries": ["3.67.91.250:5050"]
}
```

Restart Docker:

```bash
sudo systemctl restart docker
```

---

# 10. Fix GitLab CI Job Token

```bash
sudo gitlab-rails runner 'key = OpenSSL::PKey::RSA.new(2048).to_pem; s = ApplicationSetting.current; s.ci_job_token_signing_key = key; s.save!'
```

Restart GitLab:

```bash
sudo gitlab-ctl restart
```

---

# 11. Application Code

## app.py

```python
from flask import Flask
app = Flask(__name__)

@app.route("/")
def index():
    return "Hello DevOps!"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

requirements.txt

```
Flask
gunicorn
psycopg2-binary
```

---

# 12. Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 5000

CMD ["python", "app.py"]
```

---

# 13. GitLab CI Pipeline

.gitlab-ci.yml

```yaml
stages:
  - build

build:
  stage: build
  script:
    - docker login http://3.67.91.250:5050 -u $CI_REGISTRY_USER -p $CI_REGISTRY_PASSWORD
    - docker build -t 3.67.91.250:5050/root/python-auth-app:$CI_COMMIT_SHORT_SHA .
    - docker push 3.67.91.250:5050/root/python-auth-app:$CI_COMMIT_SHORT_SHA
```

---

# 14. Helm Chart

Chart.yaml

```yaml
apiVersion: v2
name: python-auth-app
version: 0.1.0
```

values.yaml

```yaml
image:
  repository: 3.67.91.250:5050/root/python-auth-app
  tag: latest
```

---

# 15. Deploy with Helm

```bash
helm install auth-app app/python-auth-k8s
```

Verify:

```bash
kubectl get pods
kubectl get svc
```

---

# 16. Optional: ArgoCD

Install:

```bash
kubectl create namespace argocd

kubectl apply -n argocd \
https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

Port-forward:

```bash
kubectl port-forward svc/argocd-server -n argocd 8080:443
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
GitLab Registry
   ↓
Helm
   ↓
Kubernetes
```
"""

output = "/mnt/data/README.md"
pypandoc.convert_text(text, "md", format="md", outputfile=output, extra_args=['--standalone'])

output