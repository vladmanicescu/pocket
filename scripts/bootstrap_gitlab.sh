#!/usr/bin/env bash
set -euo pipefail

GITLAB_IP="${1:-}"
GITLAB_TOKEN="${2:-}"

if [[ -z "$GITLAB_IP" || -z "$GITLAB_TOKEN" ]]; then
  echo "Usage: $0 <gitlab_ip> <gitlab_token>"
  exit 1
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="$ROOT_DIR/app/python-auth-app"
K8S_DIR="$ROOT_DIR/app/python-auth-k8s"

APP_REMOTE="http://oauth2:${GITLAB_TOKEN}@${GITLAB_IP}/root/python-auth-app.git"
K8S_REMOTE="http://oauth2:${GITLAB_TOKEN}@${GITLAB_IP}/root/python-auth-k8s.git"

push_repo() {
  local dir="$1"
  local remote_url="$2"

  cd "$dir"

  if [[ ! -d .git ]]; then
    git init
  fi

  git checkout -B main
  git config user.name "bootstrap"
  git config user.email "bootstrap@example.local"

  git add .
  git commit -m "Initial bootstrap" || true

  if git remote get-url origin >/dev/null 2>&1; then
    git remote set-url origin "$remote_url"
  else
    git remote add origin "$remote_url"
  fi

  git push -u origin main --force
}

push_repo "$APP_DIR" "$APP_REMOTE"
push_repo "$K8S_DIR" "$K8S_REMOTE"

echo "Done:"
echo "  - python-auth-app pushed"
echo "  - python-auth-k8s pushed"