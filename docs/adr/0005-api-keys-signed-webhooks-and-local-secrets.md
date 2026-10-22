# 0005. API keys for callers, HMAC for webhooks, replays dropped; how secrets live locally

Date: 2026-10-22. Status: accepted.

## Decision

- **Callers** (n8n, people, scripts) send `X-API-Key`. The hub keeps only the key's SHA-256 and compares digests
  with `hmac.compare_digest`; with no key configured, every protected route answers 503 rather than running open.
  A per-address rate limit (slowapi) sits on every route, and on the run endpoints a tighter one (6 a minute). The
  limiter is created inside `create_app`, because slowapi accumulates limits on a module-level limiter across app
  instances (seen in an earlier project).
- **CRM webhooks** are believed only with a valid EspoCRM signature: `Signature: base64(webhookId:hex(HMAC-SHA256(
  body, secret)))`, checked in constant time against the secret the bootstrap stored when it created the
  subscription. A test pins the signature to one computed by PHP's `hash_hmac` in the EspoCRM container, so the
  hub and EspoCRM cannot drift apart. Bodies over 1 MB are refused; records must be `[{"id": <alphanumeric>}]`.
- **Replays.** EspoCRM's signature has no timestamp, so a replay window cannot be enforced on time. Instead the hub
  stores the SHA-256 of every accepted body and acknowledges a repeat without acting on it. Acting twice would be
  harmless anyway (the sync is idempotent, ADR 0003); dropping replays keeps it from being a way to load the ERP.
- **Webhooks only start work.** A delivery names deal ids; the hub re-reads each deal from the CRM with its own API
  key before invoicing, so a forged or stale payload can at most cause a re-read.

## Secrets in the local stack

`make env` generates every password and key into `.env`; the Odoo, EspoCRM and Zammad bootstraps mint API keys into
`.secrets/`. Both are git-ignored, and `scripts/secret_scan.sh` runs gitleaks over the history and over exactly the
files git would pick up. The files in `.secrets/` are world-readable (0644) because the hub's container runs as a
different user than the containers that write them; that is acceptable for a developer machine and nowhere else.
A deployment reads them from the platform's secret store (OCI Vault in `infra/terraform`, or Docker secrets).
Real-account credentials (Salesforce, Jira, GitHub) are never in either file: `HUB_*_FILE` variables point at paths
outside the repository (docs/DEPLOY.md).
