-- Sync state. The ERP is the source of truth for "is this deal invoiced" (looked up by the invoice's source
-- document); these tables are the hub's own memory: where it got to, what it linked, what it could not process.

CREATE TABLE sync_cursor (
    source      text PRIMARY KEY,
    cursor_at   timestamptz NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE deal_link (
    crm              text NOT NULL,
    deal_id          text NOT NULL,
    erp_invoice_id   integer NOT NULL,
    erp_invoice_ref  text,
    erp_customer_id  integer NOT NULL,
    amount           numeric(14, 2) NOT NULL,
    currency         char(3) NOT NULL,
    synced_at        timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (crm, deal_id)
);

CREATE TABLE dead_letter (
    id           bigserial PRIMARY KEY,
    crm          text NOT NULL,
    deal_id      text NOT NULL,
    reason       text NOT NULL,      -- permanent | transient
    detail       text NOT NULL,
    attempts     integer NOT NULL DEFAULT 1,
    first_seen   timestamptz NOT NULL DEFAULT now(),
    last_seen    timestamptz NOT NULL DEFAULT now(),
    resolved_at  timestamptz
);
CREATE UNIQUE INDEX dead_letter_open ON dead_letter (crm, deal_id) WHERE resolved_at IS NULL;

-- Outbox for CRM write-backs: recorded in the same transaction as the link, delivered after, retried until done.
CREATE TABLE crm_writeback (
    crm             text NOT NULL,
    deal_id         text NOT NULL,
    invoice_ref     text,
    billing_status  text NOT NULL,
    attempts        integer NOT NULL DEFAULT 0,
    last_error      text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    done_at         timestamptz,
    PRIMARY KEY (crm, deal_id)
);

CREATE TABLE sync_run (
    id           bigserial PRIMARY KEY,
    crm          text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       text NOT NULL DEFAULT 'running',  -- running | succeeded | failed
    stats        jsonb NOT NULL DEFAULT '{}'::jsonb
);
