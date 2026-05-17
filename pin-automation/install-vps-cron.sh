#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/marketing-automation}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CRON_TIME="${CRON_TIME:-10 6 * * *}"
LOG_FILE="${LOG_FILE:-/var/log/pinterest-buffer-publisher.log}"

cd "$APP_DIR"

"$PYTHON_BIN" -m venv .venv
. .venv/bin/activate
pip install -r pin-automation/requirements.txt

if [ ! -f .env ]; then
  cp pin-automation/.env.example .env
  chmod 600 .env
  echo "Created $APP_DIR/.env. Fill it with real secrets before the cron job runs."
fi

CRON_CMD="cd $APP_DIR && set -a && . ./.env && set +a && ./.venv/bin/python pin-automation/publisher.py >> $LOG_FILE 2>&1"
TMP_CRON="$(mktemp)"
crontab -l 2>/dev/null | grep -v "pin-automation/publisher.py" > "$TMP_CRON" || true
printf '%s %s\n' "$CRON_TIME" "$CRON_CMD" >> "$TMP_CRON"
crontab "$TMP_CRON"
rm -f "$TMP_CRON"

echo "Installed cron schedule: $CRON_TIME"
echo "Command: $CRON_CMD"
