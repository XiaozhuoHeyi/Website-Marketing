#!/usr/bin/env bash
set -euo pipefail

repo="${1:-XiaozhuoHeyi/Fix-website-problems}"
tmpdir="$(mktemp -d)"

gh repo clone "$repo" "$tmpdir"
mkdir -p "$tmpdir/.github/workflows"
cp .github/workflows/pinterest-buffer-publisher.yml "$tmpdir/.github/workflows/pinterest-buffer-publisher.yml"
cp .github/workflows/deploy-pinterest-n8n.yml "$tmpdir/.github/workflows/deploy-pinterest-n8n.yml"

cd "$tmpdir"
git add .github/workflows
git commit -m "Add Pinterest automation workflows"
git push
