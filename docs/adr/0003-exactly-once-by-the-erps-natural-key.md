# 0003. Exactly-once invoicing by the ERP's natural key, an advisory lock and an outbox

Date: 2026-10-22. Status: accepted.

## Context

Double-invoicing a customer is the worst thing this system can do; missing an invoice is the second worst. The
hub can crash at any point, Odoo can time out after committing, and n8n may trigger a run while another is going.

## Decision

1. **The ERP is the source of truth for "already invoiced".** Every invoice carries `invoice_origin =
   <crm>:<deal id>`. Before creating anything, the sync looks up invoices with that origin. A found draft is
   posted; a found posted invoice is linked; only when there is none does it create one. So a crash anywhere
   between "create" and "record" heals on the next run, without a distributed transaction.
2. **Creates are never retried blindly.** The HTTP layer retries reads, and retries writes only when the
   connection never opened or the server said 429. A timeout after the request was sent raises `AmbiguousWrite`;
   the sync then looks the invoice up by its origin before deciding.
3. **One run per CRM at a time**: a Postgres advisory lock (`pg_try_advisory_lock`), so two runs cannot both miss
   the lookup and both create. A second run fails fast with `AlreadyRunning`.
4. **CRM write-backs go through an outbox** (`crm_writeback`), written in the same transaction as the link, sent
   after, retried on the next run until delivered. A CRM outage never blocks invoicing.
5. **A cursor with an overlap.** Deals are read by modification time from the last cursor minus five minutes, so
   clock skew and same-second updates cannot drop a deal; re-reading is harmless because of (1).
6. **Dead letters** record deals that need a person (bad data) or that failed transiently after retries, with the
   reason; fixing the deal in the CRM moves its modification time past the cursor, the next run invoices it and
   resolves the dead letter.

## Evidence

`tests/unit/test_sync_engine.py` crashes the run (a `BaseException` nothing can swallow) before posting and before
recording, loses a create's response, fails write-backs and parks a transient ERP outage; every case ends with
exactly one posted invoice per deal. `tests/integration/test_live_sync.py` repeats the crash against the real Odoo.

## Consequences

- An invoice cancelled in Odoo for a deal that is still won is not re-issued: a person decided that, so the
  reconciliation reports it instead (Sprint 2).
- The overlap means each run re-reads the last five minutes of deals, including the ones it just wrote back to.
