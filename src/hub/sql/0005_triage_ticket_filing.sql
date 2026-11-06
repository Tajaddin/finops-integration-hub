-- The ticket for a triage run is filed after the run is saved, so a ticket system that fails cannot lose the run.
-- The caller's retry (same Idempotency-Key) files what is missing: ticket_noted_at is set once the ticket and its
-- draft note both exist, and ticket_filing_until is a short lease so two overlapping retries cannot both file one.
ALTER TABLE triage_run ADD COLUMN ticket_noted_at timestamptz, ADD COLUMN ticket_filing_until timestamptz;
UPDATE triage_run SET ticket_noted_at = received_at WHERE ticket_id IS NOT NULL;
