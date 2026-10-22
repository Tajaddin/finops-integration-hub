-- Reconciliation runs and their exceptions, and the hub's memory of which ticket it filed for which exception.

CREATE TABLE recon_run (
    id           bigserial PRIMARY KEY,
    crm          text NOT NULL,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    stats        jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE recon_exception (
    run_id       bigint NOT NULL REFERENCES recon_run (id) ON DELETE CASCADE,
    fingerprint  text NOT NULL,
    kind         text NOT NULL,
    key          text NOT NULL,
    severity     text NOT NULL,
    summary      text NOT NULL,
    detail       jsonb NOT NULL,
    PRIMARY KEY (run_id, fingerprint)
);

-- One row per exception fingerprint that ever got a ticket. Open while closed_at is null.
CREATE TABLE ticket_link (
    fingerprint     text NOT NULL,
    system          text NOT NULL,      -- zammad | github | jira
    kind            text NOT NULL,
    key             text NOT NULL,
    ticket_id       text NOT NULL,
    ticket_ref      text,
    opened_at       timestamptz NOT NULL DEFAULT now(),
    last_seen_run   bigint,
    closed_at       timestamptz,
    PRIMARY KEY (system, fingerprint, opened_at)
);
CREATE UNIQUE INDEX ticket_link_open ON ticket_link (system, fingerprint) WHERE closed_at IS NULL;

-- Deliveries already accepted from a CRM webhook (body digest), so a replayed delivery is acknowledged and ignored.
CREATE TABLE webhook_delivery (
    digest       text PRIMARY KEY,
    source       text NOT NULL,
    received_at  timestamptz NOT NULL DEFAULT now(),
    records      integer NOT NULL
);
