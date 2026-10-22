# 0004. Zammad without Elasticsearch; tickets keyed by exception fingerprint in the hub's own database

Date: 2026-10-22. Status: accepted.

## Context

Finance ops works its exceptions in a helpdesk, not in a spreadsheet. The postings name Jira, Zendesk, ServiceNow
and Salesforce Service Cloud (OPSWAT, DIRECTV, Datacor); all need accounts. The local stack also has to fit the
16 GB Docker VM next to Odoo, EspoCRM, n8n and Langfuse.

## Decision

- **Zammad 7.2** (open source, Zendesk-like: groups, priorities, tags, an API with tokens) runs in the stack from
  the upstream compose services, on the shared Postgres, with Elasticsearch disabled
  (`ELASTICSEARCH_ENABLED=false`). Elasticsearch is the memory hog of a Zammad stack; without it Zammad falls back
  to database search, which a queue of a few hundred tickets does not notice.
- A one-shot `zammad-bootstrap` container (`rails r`) finishes the setup wizard, creates the Finance Ops, Billing,
  Collections and Support groups, an agent for the hub with an API token, and the customer that reconciliation
  tickets are filed for.
- **The hub remembers which ticket it filed for which exception** (`ticket_link`, keyed by the exception's
  fingerprint: a hash of its class and key). It does not search the ticket system for its own tickets: searching
  needs Elasticsearch in Zammad, and costs API quota in Jira and GitHub. The ticket system stays the source of truth
  for whether a ticket is open (`is_open`), so a ticket closed by a person is noticed.
- GitHub Issues and Jira Cloud are the same `TicketSink` interface, contract-tested against the response shapes in
  their API docs; switching is `HUB_TICKETS=github|jira`.

## Consequences

- One ticket per exception while it is detected; a rerun files nothing (measured: 86 opened, then 0 on the rerun,
  `reports/recon/eval.json`); a later run that no longer finds the exception closes the ticket with a note.
- If a person closes a ticket while the problem is still there, the next run files a new one that names the old
  one, rather than re-opening a ticket a person decided to close.
- Without Elasticsearch, Zammad's full-text search over ticket bodies is slower; for a large helpdesk, enable it.
