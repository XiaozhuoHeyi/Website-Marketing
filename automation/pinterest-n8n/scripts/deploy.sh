#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/pinterest-n8n}"
REPO_DIR="${REPO_DIR:-$PWD}"
WORKFLOW_SOURCE="${WORKFLOW_SOURCE:-N8N/n8n Pinterest Auto Publish Process Build/pinterest-buffer-periodic-publisher.workflow.json}"

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is required on the VPS." >&2
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 is required on the VPS." >&2
  exit 1
fi

mkdir -p "$APP_DIR/workflows" "$APP_DIR/backups"
cp "$REPO_DIR/automation/pinterest-n8n/docker-compose.yml" "$APP_DIR/docker-compose.yml"

if [ ! -f "$APP_DIR/.env" ]; then
  cp "$REPO_DIR/automation/pinterest-n8n/.env.example" "$APP_DIR/.env"
  echo "Created $APP_DIR/.env. Fill it in, then rerun this script." >&2
  exit 2
fi

cp "$REPO_DIR/$WORKFLOW_SOURCE" "$APP_DIR/workflows/pinterest-buffer-periodic-publisher.workflow.json"

cd "$APP_DIR"
docker compose pull
docker compose up -d

echo "Waiting for n8n to become ready..."
for _ in $(seq 1 60); do
  if docker compose exec -T n8n wget -qO- http://127.0.0.1:5678/healthz >/dev/null 2>&1; then
    break
  fi
  sleep 3
done

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
if docker compose exec -T n8n n8n export:workflow --all --output="/home/node/.n8n/workflows-backup-$timestamp.json" >/dev/null 2>&1; then
  docker compose cp "n8n:/home/node/.n8n/workflows-backup-$timestamp.json" "./backups/workflows-backup-$timestamp.json" >/dev/null 2>&1 || true
fi

docker compose exec -T n8n n8n import:workflow --input=/workflows/pinterest-buffer-periodic-publisher.workflow.json

cat <<'MSG'
Deploy complete.

Open n8n, verify credentials for:
- Google Sheets account
- OpenAI account
- Buffer API

Then activate the imported workflow if it is not already active.
MSG
