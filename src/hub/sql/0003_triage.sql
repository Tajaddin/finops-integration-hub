-- Every triaged message, what the agent decided, the ticket it became, and the approval decision when one was needed.

CREATE TABLE triage_run (
    id               bigserial PRIMARY KEY,
    received_at      timestamptz NOT NULL DEFAULT now(),
    sender           text NOT NULL,
    message          text NOT NULL,
    result           jsonb NOT NULL,
    needs_approval   boolean NOT NULL,
    ticket_system    text,
    ticket_id        text,
    ticket_ref       text,
    decision         text CHECK (decision IN ('approved', 'rejected')),
    decided_by       text,
    decided_at       timestamptz
);
CREATE INDEX triage_run_pending ON triage_run (received_at) WHERE needs_approval AND decision IS NULL;
