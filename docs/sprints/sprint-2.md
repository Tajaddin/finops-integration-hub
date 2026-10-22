# Sprint 2 retro: reconciliation and tickets

Dates: 2026-10-22. Milestone: [Sprint 2 - Reconciliation and tickets](https://github.com/Tajaddin/finops-integration-hub/milestone/2).

**Goal:** a reconciliation run finds every seeded mismatch between CRM and ERP, measured with precision and recall per
class, and files one deduplicated helpdesk ticket per exception. **Met.**

## Delivered

| Issue | Points | Result |
|---|---|---|
| #7 reconciliation | 5 | eight exception classes with stable fingerprints; a fault injector that edits both systems as people do and writes the ground truth |
| #8 Zammad | 5 | Zammad 7.2 in the stack without Elasticsearch, bootstrapped headless; one ticket per exception, closed when resolved, refiled after a manual close ([ADR 0004](../adr/0004-zammad-without-elasticsearch-tickets-keyed-by-fingerprint.md)) |
| #9 GitHub and Jira | 3 | same interface, contract-tested on the vendors' documented shapes |
| #10 webhooks and write-back | 3 | signed EspoCRM webhooks into the hub (signature pinned to PHP's `hash_hmac`), replays dropped, billing status written back ([ADR 0005](../adr/0005-api-keys-signed-webhooks-and-local-secrets.md)) |

16 of 16 committed points done.

## Measured (300-account book, seed 42; one machine, Docker Desktop)

- First sync (`reports/sync/first-run.json`): 300 won deals, **286 invoices created, exactly the 14 deals with
  seeded data problems dead-lettered** (7 missing plans, 4 mistyped amounts, 3 unapproved discounts), 185 customers
  created and 31 existing ones matched by website, 92 s (p50 274 ms per deal). Rerun: 0 created, 286 unchanged, 5.8 s.
- Reconciliation (`reports/recon/eval.json`): 86 exceptions in the truth (72 injected, 14 from the book),
  **86 detected, precision 1.0 and recall 1.0 in every class**, 0 false positives across 644 deals, 294 invoices
  and 224 customers. 86 Zammad tickets opened, **0 on the rerun**, 16 closed by the repair run (8 drifts fixed by
  the status write-back, 8 deals invoiced by the sync).
- Webhook path (`reports/sync/webhook.json`): deal won in EspoCRM to posted Odoo invoice, 5/5 delivered, median
  9.4 s, max 10.4 s (EspoCRM delivers from a job that runs every minute).

## What went well

- A rule-based reconciler on a known ground truth is the right place for 100%: the useful part of the measurement
  is the zero false positives on a realistic book, and the fault injector that makes the claim checkable.
- Pinning the webhook signature to a value computed by PHP in the EspoCRM container caught that my first test vector
  was invented.

## What did not

- The hub could not read secrets the Odoo and Zammad containers wrote with mode 0600 (another uid); the first live
  webhook was accepted and then failed in the background. Local secret files are now 0644, documented in ADR 0005.
- EspoCRM blocks webhooks to private addresses unless they are allow-listed as `host:port`, and its
  `config:set` command stores arrays as strings; a one-shot container now writes the allowlist into its config.
- EspoCRM's `in` filter needs PHP array syntax (`where[0][value][]`); the measurement script found it with a 400.
- In CI the reconciliation eval reported one false positive: a deal with a data problem from the integration test's
  own book, which the truth (built from the CI book only) did not list. The reconciler was right; the truth now
  includes the measured state before any fault is injected, and CI checks that the book's own problems are in it.
- `gitleaks` scans every branch, so the Sprint 1 PR failed on a Sprint 2 report file (EspoCRM ids look like keys
  to its generic rule); an allowlist scoped to `reports/` with the exact id pattern fixed both branches.

## Carried into Sprint 3

- Nothing open. Live Jira stays in the Backlog (#20, needs an account).
