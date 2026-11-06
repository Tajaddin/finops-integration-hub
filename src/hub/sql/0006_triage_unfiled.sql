-- /metrics counts the runs whose ticket is still not filed (hub_triage_unfiled) on every scrape; this index keeps
-- that count to the few rows it is looking for.
CREATE INDEX triage_run_unfiled ON triage_run (received_at) WHERE ticket_noted_at IS NULL AND idempotency_key IS NOT NULL;
