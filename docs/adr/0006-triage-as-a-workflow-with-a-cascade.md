# 0006. Triage as a LlamaIndex Workflow with a classifier cascade; money decisions in code

Date: 2026-10-22. Status: accepted.

## Context

Inbound billing and support messages need an intent, a queue, a priority, a drafted reply that cites policy, and,
for refunds, a decision on whether a person must approve first. Akoncagua asks for "agent workflows for document
intake, classification" with "evaluation harnesses" and awareness of "prompt-injection risks, least-privilege
design"; Apptegy for "informed opinions about when each is the wrong choice"; Infosys names LlamaIndex.

## Decision

- **A LlamaIndex `Workflow`, not a free-roaming tool-calling agent.** Three steps (classify, enrich, draft) with
  typed events. The only tool is a read-only invoice lookup, and the workflow passes it only invoice numbers that
  appear in the message, so "look up every customer" in a message has nothing to call. Approval of refunds above
  the threshold (or of unknown size) is an `if` in code, after the model; a draft that promises a held refund or
  quotes the system prompt (a canary string) is replaced. The message is wrapped as data and cannot close its own
  delimiter. Ticket creation happens in the API after the workflow, never as a model tool.
- **A cascade.** A TF-IDF + logistic-regression model answers when its confidence is at least 0.9; otherwise the
  LLM classifies with the 8 most similar labelled messages retrieved from pgvector as examples. The baseline costs
  about 80 microseconds and no tokens; the LLM is paid for only where the cheap model is unsure. The measured trade
  is in `reports/triage/eval.json`.
- **Local model through Ollama** (`qwen2.5:7b`, a non-thinking model: thinking models spend their budget on hidden
  reasoning and return empty JSON) and `nomic-embed-text` embeddings, so the evaluation is repeatable without paid
  API keys. The model is a setting (`HUB_TRIAGE_MODEL`); CI uses a deterministic stand-in to test the wiring and is
  never the source of a model number.

## Evaluation design

Bitext's support set is synthetic and templated, so its own test split is close to its training split: a TF-IDF
model scores 99.9% on it, which says more about the data than about any model. The comparison that matters is a
**challenge set of 90 hand-written B2B e-mails** (longer, messier, with signatures, invoice numbers and nine
prompt-injection attempts), labelled by the author and committed before any model ran on it, so prompts could not
be tuned to it. Both sets are reported, with confidence intervals.

## Consequences

- Free-form agent behaviour (multi-step tool use) is deliberately not available. If a later feature needs it, it
  gets its own tools with their own least-privilege scope and its own evaluation.
- The cascade threshold is a cost/accuracy knob; changing it means re-running the evaluation.
