# Pinterest Automation Runtime

This folder turns the existing n8n workflow into an always-on VPS service.

It runs:

- `n8n` for the scheduled workflow
- `postgres` for reliable n8n state and credential storage
- a deploy script that imports `N8N/n8n Pinterest Auto Publish Process Build/pinterest-buffer-periodic-publisher.workflow.json`
- GitHub Actions SSH deployment support

The workflow trigger is currently `0 8 * * *` in `Europe/Paris`. It prepares up to 3 daily Buffer slots for 09:00, 13:00, and 18:00 Europe/Paris.

## VPS Setup

On a fresh Ubuntu VPS:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo tee /etc/apt/keyrings/docker.asc >/dev/null
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in so Docker group permissions apply.

Clone your repo on the VPS, then run:

```bash
sudo mkdir -p /opt/pinterest-n8n
sudo chown -R "$USER:$USER" /opt/pinterest-n8n
cd /path/to/Marketing
bash automation/pinterest-n8n/scripts/deploy.sh
```

The first run creates `/opt/pinterest-n8n/.env` and stops. Edit it:

```bash
sudo nano /opt/pinterest-n8n/.env
```

Generate the encryption key:

```bash
openssl rand -hex 32
```

Run deploy again:

```bash
cd /path/to/Marketing
bash automation/pinterest-n8n/scripts/deploy.sh
```

Open n8n at:

```text
http://YOUR_VPS_IP:5678
```

## Required n8n Credentials

Create or reconnect these credentials in the n8n UI:

- `Google Sheets account`
- `OpenAI account`
- `Buffer API`

The imported workflow references those credential names. n8n stores their secrets inside the persistent Docker volume, encrypted by `N8N_ENCRYPTION_KEY`.

## GitHub Actions Deploy

Add these repository secrets in GitHub:

```text
VPS_HOST
VPS_USER
VPS_SSH_KEY
VPS_PORT
VPS_APP_DIR
VPS_REPO_DIR
```

Recommended values:

```text
VPS_PORT=22
VPS_APP_DIR=/opt/pinterest-n8n
VPS_REPO_DIR=/home/YOUR_USER/Marketing
```

Set `VPS_PORT` and `VPS_APP_DIR` explicitly; the workflow reads them from secrets.

`VPS_SSH_KEY` should be a private key that can SSH into the VPS. Add the matching public key to `~/.ssh/authorized_keys` on the VPS.

When you push changes to this workflow, GitHub Actions will SSH into the VPS, pull the repository, restart n8n/Postgres, and import the workflow JSON.

## Useful Commands

```bash
cd /opt/pinterest-n8n
docker compose ps
docker compose logs -f n8n
docker compose restart n8n
docker compose exec n8n n8n list:workflow
docker compose exec n8n n8n import:workflow --input=/workflows/pinterest-buffer-periodic-publisher.workflow.json
```

## Reliability Notes

- Keep `/opt/pinterest-n8n/.env` backed up securely, especially `N8N_ENCRYPTION_KEY`.
- Do not delete Docker volumes unless you have exported credentials/workflows first.
- Use a domain plus HTTPS reverse proxy before serious production use.
- Keep the VPS timezone independent from the workflow; the workflow uses `Europe/Paris` explicitly.
