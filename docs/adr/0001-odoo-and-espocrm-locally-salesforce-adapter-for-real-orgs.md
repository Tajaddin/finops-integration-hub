# 0001. Odoo and EspoCRM as the local ERP and CRM; a Salesforce adapter for real orgs

Date: 2026-10-22. Status: accepted (spike #3).

## Context

The postings this project answers name NetSuite and Salesforce (Apptegy), "ERP, CRM, CPQ" (Apptegy, Datacor),
a B2B CRM (Clerkie). Neither NetSuite nor Salesforce can run locally, and the integration has to be built and tested
without vendor accounts. What matters is two separate systems with real data models and real APIs, so the hub has
to deal with what integration work actually is: different ids, different field names, paging, rate limits,
partial failures, and a reconciliation that proves both sides agree.

## Spike result

- **Odoo 19 Community** (`odoo:19.0-20261015`) serves the External JSON-2 API (`POST /json/2/<model>/<method>`,
  `Authorization: bearer <key>`) from its `rpc` module, which auto-installs. API keys can be minted headless with
  `res.users.apikeys._generate` inside `odoo shell` (no UI), and the CRM, Sales and Invoicing apps install without
  demo data in about 20 s. XML-RPC and JSON-RPC are deprecated in 19 (removal planned for Odoo 22), so JSON-2 is the
  API to build on.
- **EspoCRM 10** (`espocrm/espocrm:10.0.10-apache`) installs itself from environment variables, runs on the shared
  Postgres (`ESPOCRM_DATABASE_PLATFORM=Postgresql`), and its REST API creates custom fields, a role and an API user
  whose key comes back in the response. Webhooks are created over the API and signed with HMAC-SHA256.

## Decision

- ERP: Odoo 19 Community. CRM: EspoCRM 10. Both bootstrap themselves (`docker/odoo/`, `hub bootstrap espocrm`)
  with least-privilege integration users: Odoo's has the Sales and Billing groups only; EspoCRM's role reads and
  edits Accounts and Opportunities, creates nothing but Accounts, deletes nothing.
- The sync talks to two interfaces (`CrmSource`, `ErpSink`), not to vendors. A `SalesforceCrm` adapter (OAuth 2.0
  client credentials, SOQL paging, sObject PATCH) is tested against the response shapes in Salesforce's REST docs
  and waits for a Developer Edition org (backlog #19). Its custom fields are in `salesforce/`.

## Alternatives

- **Twenty** (open-source CRM, popular in 2026): no documented way to create an API key without the UI, so CI could
  not bootstrap it.
- **ERPNext**: a strong open-source ERP, but its stack (Frappe, MariaDB, Redis, workers) is heavier than Odoo's
  for the same invoice and payment model, and Odoo is the more common skill in postings.
- **Two Odoo databases** (one as CRM, one as ERP): same software on both sides hides exactly the mapping problems
  the project is about.
- **A Salesforce stub as the only CRM**: a test double is not a system; it would only prove the hub agrees with
  itself.

## Consequences

- One Postgres serves Odoo, EspoCRM, the hub, n8n, Zammad and Langfuse, one database and login each, which keeps
  the local stack inside the 16 GB Docker VM. Production would give each system its own.
- EspoCRM has no opportunity line items in its free edition, so plan, seats, term and discount are custom fields on
  the deal, as many companies do in Salesforce too.
