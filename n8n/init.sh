#!/bin/sh
# One-shot n8n setup (the n8n-init service): import the two credentials the workflows use, then import and publish
# every workflow in n8n/workflows (file name = workflow id). Idempotent: imports overwrite by id.
set -eu

# Credentials go through a temporary file so the hub's API key never sits in the repository or an image layer.
# n8n encrypts them with N8N_ENCRYPTION_KEY on import.
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
cat > "$tmp" <<EOF
[
  {"id": "hubApiKey0000001", "name": "Hub API key", "type": "httpHeaderAuth",
   "data": {"name": "X-API-Key", "value": "${HUB_API_KEY}"}},
  {"id": "n8nWebhookKey001", "name": "Webhook key", "type": "httpHeaderAuth",
   "data": {"name": "X-Webhook-Key", "value": "${N8N_WEBHOOK_KEY}"}},
  {"id": "mailpitSmtp00001", "name": "Mailpit SMTP", "type": "smtp",
   "data": {"host": "mailpit", "port": 1025, "secure": false, "disableStartTls": true, "user": "", "password": ""}}
]
EOF
n8n import:credentials --input="$tmp"
n8n import:workflow --separate --input=/bootstrap/workflows
for file in /bootstrap/workflows/*.json; do
  id=$(basename "$file" .json)
  n8n publish:workflow --id="$id"
done
echo "n8n: credentials and $(ls /bootstrap/workflows/*.json | wc -l) workflows imported and published"
