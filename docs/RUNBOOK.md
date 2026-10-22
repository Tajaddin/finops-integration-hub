# Runbook

One section per alert in `observability/alert_rules.yml`. Every command assumes the repository root with `.env`
loaded (`set -a; . ./.env; set +a`).

## HubDown

The hub's `/metrics` cannot be scraped. Syncs, webhooks and triage stop; n8n's calls fail and it mails finance ops.

1. `docker compose ps hub` and `docker compose logs --tail 100 hub`.
2. A `PermissionError` on `/secrets/...` means a bootstrap rewrote a secret file with the wrong mode: re-run the
   bootstrap (`docker compose up odoo-init zammad-bootstrap`, `uv run hub bootstrap espocrm`).
3. `curl -s 127.0.0.1:8000/ready`: 503 with `database` means Postgres; `docker compose ps pg`.
4. Nothing is lost while it is down: the next sync reads from its cursor, and EspoCRM retries webhook deliveries
   for ten minutes; anything older is caught by the 15-minute scheduled sync.

## SyncStale

No sync has succeeded for an hour although n8n runs one every 15 minutes.

1. n8n's execution list (`http://127.0.0.1:5678`, workflow "CRM to ERP sync every 15 minutes") shows the error;
   or run one by hand: `uv run hub sync`.
2. `409 another sync:espocrm run holds the lock`: a run is stuck. Find it in `sync_run` (status `running`) and
   the hub's logs; the advisory lock is released when its connection closes, so restarting the hub frees it.
3. Odoo or EspoCRM unreachable (`unreachable: ConnectError`): check the container and its health check. Runs are
   idempotent, so retrying after the fix is always safe (ADR 0003).

## DeadLettersPilingUp

More than 20 won deals have waited 30 minutes for a person.

1. `uv run hub dead-letters` lists each deal with the reason: `missing plan`, `amount ... differs from the price
   book`, `discount ...% is not an approved discount`, or a parked ERP error (`reason: transient`).
2. Bad data is fixed in the CRM by sales ops; the next sync sees the change (its modification time moves past the
   cursor), invoices the deal and resolves the dead letter.
3. Transient ones: once the ERP is healthy, `uv run hub sync --full` re-tries them.

## MoneyMismatchFound

Reconciliation found an invoice that disagrees with its deal on money (amount, currency, a duplicate invoice, or an
invoice for a deal that is no longer won). Each one has a ticket in the Billing queue with both values.

1. Decide which side is right with the account manager. If the invoice is wrong, credit and reissue it in Odoo; if
   the deal is wrong, correct it in the CRM.
2. The next reconciliation run (nightly, or `uv run hub recon`) closes the ticket itself when both sides agree.

## WebhookSignatureFailures

More than five CRM webhook deliveries in ten minutes failed the HMAC check.

1. If the EspoCRM bootstrap was re-run, the webhook secret rotated: the hub reads the secret file on every delivery,
   so check `.secrets/espocrm_webhook_secret` is readable by the hub container.
2. Otherwise treat it as forged traffic: the endpoint should not be reachable from outside (see the Caddy rules in
   `infra/terraform/cloud-init.yaml`). Forged deliveries cannot invoice anything (the hub re-reads every deal with
   its own key), but find the source.

## TriageSlow

Triage p95 above 20 s for ten minutes.

1. `ollama ps`: a second model resident on the 8 GB GPU forces spill into system memory; `ollama stop <model>`.
2. `nvidia-smi` shows another process on the GPU (training, a game): it competes for the same memory.
3. While slow, n8n retries the call (5 tries, 5 s apart) with the same Idempotency-Key, so nothing is filed twice.

## HubErrorRate

More than 5% of API calls fail. The hub's JSON logs name the failing dependency (`system=odoo|espocrm|zammad`).
Fix the dependency; the callers (n8n, EspoCRM) retry, and every write path is idempotent.
