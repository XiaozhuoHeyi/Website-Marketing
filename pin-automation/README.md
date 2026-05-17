# Pinterest Buffer Publisher

Small always-on replacement for the n8n timer workflow:

```text
Google Sheets queue -> OpenAI Pin copy -> Buffer scheduled Pinterest post -> Google Sheets state update
```

It is safe to run from GitHub Actions or a VPS cron. The Google Sheet stores
published image URLs, so a retry should not duplicate successful Pinterest pins.

## Files

```text
pin-automation/publisher.py
pin-automation/requirements.txt
pin-automation/.env.example
.github/workflows/pinterest-buffer-publisher.yml
```

## Google Sheet

The default sheet is:

```text
Spreadsheet ID: 1ZGszsqsey9Pu08TrUMGq6gksatbHe2WNMLcw6fWV5BU
Sheet: Sheet1
```

The script reads product rows from these columns when present:

```text
product_id
sku
title
url
primary_image
gallery_image_1
gallery_image_2
gallery_image_3
gallery_image_4
gallery_image_5
gallery_images
campaign_name
buffer_channel_1_id
pinterest_channel_id
pinterest_board_service_id
pin_status
pin_priority
published_image_urls
```

If output columns such as `pin_title`, `buffer_post_id`, or
`published_image_urls` are missing, the script adds them to the header row.
It also maintains `pin_description_history` so future descriptions avoid
repeating older copy for the same product.

Rows are skipped only when `pin_status` is `completed`, `skip`, or `skipped`.
Each run chooses the next unpublished image from `primary_image` then
`gallery_image_1..5`.
After Buffer accepts a Pinterest post, the image URL is recorded permanently in
`published_image_urls`. When every image for one product has been used, the row
becomes `completed` and the next run moves to the next product.

## GitHub Actions Setup

1. Push this workspace to a GitHub repository.
2. In GitHub, open `Settings -> Secrets and variables -> Actions`.
3. Add repository secrets:

```text
BUFFER_API_KEY
OPENAI_API_KEY
GOOGLE_SERVICE_ACCOUNT_JSON
```

`GOOGLE_SERVICE_ACCOUNT_JSON` should be the full service account JSON as one
secret value. Share the Google Sheet with the service account email.

4. Optional repository variables:

```text
SPREADSHEET_ID=1ZGszsqsey9Pu08TrUMGq6gksatbHe2WNMLcw6fWV5BU
SHEET_NAME=Sheet1
OPENAI_MODEL=gpt-4.1-mini
POST_TIMEZONE=Europe/Paris
POST_HOURS=9,13,18
SLOTS_PER_RUN=3
DAYS_TO_QUEUE=1
DEFAULT_PINTEREST_CHANNEL_ID=658d2612c3b752215a86a80d
DEFAULT_PINTEREST_BOARD_SERVICE_ID=1113937357772186129
```

5. Open the Actions tab and run `Pinterest Buffer Publisher` manually once with
`dry_run=true`.
6. Run again with `dry_run=false` after the payload looks right.

The schedule in `.github/workflows/pinterest-buffer-publisher.yml` runs daily at
`06:10 UTC`. Buffer posts are scheduled by the script for Europe/Paris
`09:00`, `13:00`, and `18:00`.

## VPS Setup

On the server:

```bash
cd /opt
git clone YOUR_REPO_URL marketing-automation
cd marketing-automation
bash pin-automation/install-vps-cron.sh
```

Edit `.env` with real values. The installer adds this cron shape:

```cron
10 6 * * * cd /opt/marketing-automation && set -a && . ./.env && set +a && ./.venv/bin/python pin-automation/publisher.py >> /var/log/pinterest-buffer-publisher.log 2>&1
```

For a systemd timer, create:

```text
/etc/systemd/system/pinterest-buffer-publisher.service
```

```ini
[Unit]
Description=Pinterest Buffer Publisher

[Service]
Type=oneshot
WorkingDirectory=/opt/marketing-automation
EnvironmentFile=/opt/marketing-automation/.env
ExecStart=/opt/marketing-automation/.venv/bin/python /opt/marketing-automation/pin-automation/publisher.py
```

```text
/etc/systemd/system/pinterest-buffer-publisher.timer
```

```ini
[Unit]
Description=Run Pinterest Buffer Publisher daily

[Timer]
OnCalendar=*-*-* 06:10:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

Enable it:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now pinterest-buffer-publisher.timer
```

## Manual Commands

Dry run:

```bash
DRY_RUN=1 python pin-automation/publisher.py
```

Live run:

```bash
python pin-automation/publisher.py
```

Enable Facebook/Instagram mirror posts too:

```bash
ENABLE_SOCIAL_MIRRORS=1 python pin-automation/publisher.py
```
