# Sprint 1 retro: systems and sync

Dates: 2026-10-22. Milestone: [Sprint 1 - Systems and sync](https://github.com/Tajaddin/finops-integration-hub/milestone/1).

**Goal:** a closed-won deal in the CRM becomes exactly one posted invoice in the ERP, through tested connectors, on a
local stack that bootstraps itself with least-privilege integration users. **Met.**

## Delivered

| Issue | Points | Result |
|---|---|---|
| #1 scaffold and CI | 2 | uv project, settings from the environment, JSON logs, `make env` for local secrets, CI with lint, unit, live integration and security jobs |
| #2 local stack | 5 | one Postgres (a database and login per system), Odoo 19 with an integration user limited to the Sales and Billing groups, EspoCRM 10 with a role scoped to Accounts and Opportunities |
| #3 spike | 2 | yes: Odoo's JSON-2 API and EspoCRM's API users work without a UI ([ADR 0001](../adr/0001-odoo-and-espocrm-locally-salesforce-adapter-for-real-orgs.md)) |
| #4 connectors | 5 | one HTTP policy for every vendor; Odoo, EspoCRM and Salesforce adapters; contract tests on Salesforce's documented shapes |
| #5 book generator | 2 | seeded, with recorded data problems and customers finance had already keyed into the ERP |
| #6 sync | 5 | exactly once by the invoice's source document, advisory lock, outbox, dead letters ([ADR 0003](../adr/0003-exactly-once-by-the-erps-natural-key.md)) |

21 of 21 committed points done.

## Measured

- First live sync of a 30-account book: 29 invoices created, 1 deal dead-lettered (an unapproved 25% discount),
  13 customers created and 6 matched by website; a rerun created nothing. Every invoice amount equalled the price
  book to the cent. (The 300-account measured run is in Sprint 2, `reports/sync/`.)

## What went well

- Spiking both vendor APIs before writing the adapters: the Odoo shell trick for API keys and EspoCRM's
  `type: api` users turned "can CI even run this" from a risk into a fact on day one.
- Writing the crash tests with a `BaseException` the engine cannot swallow made the exactly-once claim testable,
  and the same crash runs against the real Odoo.

## What did not

- A Python patch on Windows wrote CRLF line endings into the Postgres init script, so Postgres skipped it and
  every service failed to log in. Patch scripts now write with `newline="\n"`; `.gitattributes` forces LF.
- EspoCRM 10 changed its Docker volume layout (mount `data`, `custom` and `client/custom`, not the web root); the
  first stack used the old layout and was rebuilt.
- The first CI run of the live job failed: the wait script read `docker compose ps` with a tab separator, and an
  empty health field collapsed and shifted the exit code. The second failed on EspoCRM's duplicate-name prompt
  (409) when two books shared a company name; seed loads now send `X-Skip-Duplicate-Check`.
- The whole-tree secret scan scanned `.venv` and `.env` (245 false findings); it now scans exactly what git would
  pick up.

## Carried into Sprint 2

- Nothing open. The live Salesforce run stays in the Backlog (#19, needs an org).
