-- A caller that retries (n8n after a 429 or a timeout) sends the same Idempotency-Key; the hub answers with the
-- first result instead of triaging and filing a second ticket.
ALTER TABLE triage_run ADD COLUMN idempotency_key text;
CREATE UNIQUE INDEX triage_run_idempotency ON triage_run (idempotency_key) WHERE idempotency_key IS NOT NULL;
