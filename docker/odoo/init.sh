#!/usr/bin/env bash
# One-shot Odoo initialisation for the local stack: install the apps the hub integrates with, without demo data,
# then create the integration user and its API key. Idempotent: an initialised database only gets the key rotated.
set -euo pipefail

conf=/etc/odoo/odoo.conf
db_args=(--db_host "$HOST" --db_user "$USER" --db_password "$PASSWORD" -c "$conf" -d odoo --no-http)

installed=$(odoo shell "${db_args[@]}" 2>/dev/null <<<'print("ready" if env["ir.module.module"].search_count([("name", "=", "account"), ("state", "=", "installed")]) else "empty")' || true)
if ! grep -q '^ready$' <<<"$installed"; then
  echo "installing crm, sale_management, account"
  odoo server "${db_args[@]}" -i base,crm,sale_management,account --without-demo=True --stop-after-init
fi

odoo shell "${db_args[@]}" < /bootstrap/bootstrap.py
