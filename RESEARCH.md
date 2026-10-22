# Research notes (2026-10-21/22)

What I looked at before choosing this project's shape, with sources. Read with the postings in
`G:\Jobs\applications` (Apptegy AI Automation Engineer, Akoncagua AI/ML intern, DIRECTV agentic orchestration,
OPSWAT support engineering, Datacor, Clerkie, Kobie, Infosys).

## What the target employers ask for, in their words

- Apptegy: "direct API work against NetSuite, Salesforce, and Snowflake", "moving data cleanly between NetSuite,
  Salesforce, DealHub ... through APIs, webhooks, and pipelines, including the reconciliations that prove both
  sides agree", N8N, and "size it before you build it by defining the metric".
- Akoncagua: "document intake, classification, and transaction coding", "evaluation harnesses that measure
  extraction accuracy, classification quality, and end-to-end workflow outcomes", "tracing and observability for
  prompts, tool calls, failures, latency, and cost", n8n, double-entry, reconciliations, approval controls,
  prompt-injection awareness and least privilege.
- DIRECTV: run and monitor orchestration agents, "flag, log, and escalate errors", Zapier/Make, Jira.
- OPSWAT: own support tickets, search past tickets and docs before escalating, automate repetitive support steps;
  Jira, Zendesk, ServiceNow, Salesforce Service Cloud.
- Kobie: LLM observability (CloudWatch, LangSmith, Langfuse, MLflow, OpenTelemetry); evaluation harnesses in CI.
- Infosys: LangChain, LlamaIndex, MCP.

## What is respected in late-2026 portfolios

Career guides and project catalogues agree on one point: fewer projects with real evaluation, observability and
error handling beat many tutorial clones, and READMEs should explain design trade-offs and measured results.
- [5 AI portfolio projects that get you hired in 2026 (DEV)](https://dev.to/klement_gunndu/5-ai-portfolio-projects-that-actually-get-you-hired-in-2026-5bpl)
- [landedjobs/ai-engineer-portfolio-projects](https://github.com/landedjobs/ai-engineer-portfolio-projects)
- [Dataquest: AI projects sequenced for hiring](https://www.dataquest.io/blog/ai-projects/)
These are opinion, not hiring data; the postings above are the stronger evidence.

## The systems, and the free way to run each

| Need | Choice | Why / source |
|---|---|---|
| ERP | Odoo 19 Community in Docker | JSON-2 API (`/json/2/<model>/<method>`, bearer API key); XML-RPC/JSON-RPC deprecated, removal planned for Odoo 22 ([Odoo 19 External JSON-2 API](https://www.odoo.com/documentation/19.0/developer/reference/external_api.html), [External RPC API](https://www.odoo.com/documentation/master/developer/reference/external_rpc_api.html)). The `rpc` module ships in Community (checked in the image). |
| CRM | EspoCRM 10 in Docker | Installs from env vars, supports Postgres, API users and signed webhooks ([Docker install](https://docs.espocrm.com/administration/docker/installation/), [webhooks](https://docs.espocrm.com/administration/webhooks/), [API](https://docs.espocrm.com/development/api/)). |
| Salesforce | adapter + contract tests; live run needs his Developer Edition org | Developer Edition is free and includes Agentforce and Data Cloud ([Salesforce blog, 2025](https://developer.salesforce.com/blogs/2025/03/introducing-the-new-salesforce-developer-edition-now-with-agentforce-and-data-cloud)). |
| Ticketing | Zammad 7.2 self-hosted, Elasticsearch disabled | ES is the memory hog in Zammad stacks ([forum](https://community.zammad.org/t/2-5-0-memory-requirements/1073)); the compose repo has a `disable-elasticsearch-service` scenario ([zammad-docker-compose](https://github.com/zammad/zammad-docker-compose)). Jira Cloud Free needs an account; GitHub Issues works through the API. |
| Workflows | n8n 2.45 self-hosted | CLI `import:workflow`, `publish:workflow` in v2 ([CLI docs](https://docs.n8n.io/hosting/cli-commands/)). |
| LLM framework | LlamaIndex 0.14.25 with Ollama | current core release on PyPI (checked 2026-10-21). |
| LLM observability | Langfuse v4 self-hosted | web + worker + Postgres + ClickHouse + Redis + S3 (MinIO), headless init of a project and keys via `LANGFUSE_INIT_*` ([self-hosting](https://langfuse.com/self-hosting), [Docker Compose](https://langfuse.com/self-hosting/deployment/docker-compose)); LlamaIndex is traced through OpenInference's OTel instrumentation, not a Langfuse-specific integration ([Langfuse LlamaIndex guide](https://langfuse.com/integrations/frameworks/llamaindex)). |

Rejected along the way: Twenty CRM (no documented headless API-key creation,
[self-hosting docs](https://docs.twenty.com/developers/self-hosting)); LocalStack for anything AWS (since March 2026
the image needs an auth token: [testcontainers-java#11568](https://github.com/testcontainers/testcontainers-java/issues/11568)).

## Data

- CRM and ERP records: generated (`src/hub/seed`), because no public dataset has the same deals on both sides of
  an integration with known disagreements. The generator is seeded and its ground truth is written next to the
  run, so precision and recall are measurable.
- Support and billing messages for the triage agent: Bitext customer-support dataset, 26,872 utterances, 27 intents
  in 10 categories, CDLA-Sharing-1.0 ([dataset card](https://huggingface.co/datasets/bitext/Bitext-customer-support-llm-chatbot-training-dataset),
  [GitHub](https://github.com/bitext/customer-support-llm-chatbot-training-dataset)). It is synthetic (generated
  by Bitext), which the model card says.
