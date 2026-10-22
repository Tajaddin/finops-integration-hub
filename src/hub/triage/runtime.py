"""Wiring for the triage agent: the local model and embeddings through Ollama, the indexes in pgvector, the trained
baseline, and the one ERP lookup the workflow may make."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import joblib
from llama_index.core.base.base_retriever import BaseRetriever

from hub.config import Settings
from hub.connectors.odoo import INVOICE_FIELDS, OdooErp, to_invoice
from hub.triage import index
from hub.triage.baseline import TfidfBaseline
from hub.triage.data import messages, split
from hub.triage.llm import LlmClassifier
from hub.triage.workflow import TriageWorkflow

BASELINE = Path("data/triage/baseline.joblib")
EXAMPLES_TABLE = "triage_examples"
KB_TABLE = "triage_kb"


def llm(settings: Settings, model: str | None = None) -> Any:
    from llama_index.llms.ollama import Ollama

    return Ollama(
        model=model or settings.triage_model,
        base_url=settings.ollama_url,
        json_mode=True,
        temperature=0.0,
        request_timeout=120.0,
        context_window=4096,
        keep_alive="10m",
    )


def embedder(settings: Settings) -> Any:
    from llama_index.embeddings.ollama import OllamaEmbedding

    return OllamaEmbedding(model_name=settings.embed_model, base_url=settings.ollama_url, embed_batch_size=64)


def train_baseline(path: Path = BASELINE) -> TfidfBaseline:
    train, _ = split(messages())
    model = TfidfBaseline().fit(train)
    path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, path)
    return model


def load_baseline(path: Path = BASELINE) -> TfidfBaseline:
    model: TfidfBaseline = joblib.load(path)  # a file this project wrote (`hub triage-index`), never a download
    return model


def build_indexes(settings: Settings) -> dict[str, int]:
    """Embed the training messages and the help-centre articles into pgvector (replaces both tables)."""
    import psycopg

    dsn = settings.database_url.get_secret_value()
    with psycopg.connect(dsn, autocommit=True) as conn:
        for table in (EXAMPLES_TABLE, KB_TABLE):
            conn.execute(f'DROP TABLE IF EXISTS "data_{table}"')
    embed = embedder(settings)
    train, _ = split(messages())
    index.build_examples(train, embed, index.pg_store(dsn, EXAMPLES_TABLE))
    index.build_kb(embed, index.pg_store(dsn, KB_TABLE))
    return {"examples": len(train), "kb_articles": len(index.kb_articles())}


def retrievers(settings: Settings, examples_k: int = 8, kb_k: int = 3) -> tuple[BaseRetriever, BaseRetriever]:
    dsn = settings.database_url.get_secret_value()
    embed = embedder(settings)
    examples = index.load(index.pg_store(dsn, EXAMPLES_TABLE), embed).as_retriever(similarity_top_k=examples_k)
    kb = index.load(index.pg_store(dsn, KB_TABLE), embed).as_retriever(similarity_top_k=kb_k)
    return examples, kb


def invoice_lookup(erp: OdooErp) -> Callable[[str], dict[str, Any] | None]:
    """Read one invoice by its number. Read-only, and the workflow only passes numbers written in the message."""

    def lookup(number: str) -> dict[str, Any] | None:
        rows = erp.odoo.search_read(
            "account.move", [["move_type", "=", "out_invoice"], ["name", "=", number]], INVOICE_FIELDS, limit=1
        )
        if not rows:
            return None
        inv = to_invoice(rows[0])
        return {
            "number": inv.number,
            "found": True,
            "amount_untaxed": str(inv.amount_untaxed),
            "currency": inv.currency,
            "status": inv.billing_status.value,
            "due_date": inv.due_date.isoformat() if inv.due_date else None,
        }

    return lookup


def stand_in(baseline: TfidfBaseline) -> Any:
    """HUB_TRIAGE_MODEL=fake: a deterministic stand-in so CI can test the plumbing (n8n -> hub -> ticket -> approval)
    without a GPU. It answers classification with the baseline's guess and drafts a fixed reply citing the first
    article it was shown. Never used for any number in reports/triage/."""
    import json
    import re

    from llama_index.core.llms import CompletionResponse, CompletionResponseGen, CustomLLM, LLMMetadata
    from llama_index.core.llms.callbacks import llm_completion_callback

    class StandIn(CustomLLM):
        @property
        def metadata(self) -> LLMMetadata:
            return LLMMetadata(model_name="stand-in")

        @llm_completion_callback()
        def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
            if "Draft a short, polite reply" in prompt:
                cited = re.findall(r"^\[(kb-\d{3})\]", prompt, re.M)[:1]
                body = "Thank you for your message; a member of our team will follow up." + (
                    f" [{cited[0]}]" if cited else ""
                )
                return CompletionResponse(text=json.dumps({"reply": body, "citations": cited}))
            message = prompt.rsplit("<message>", 1)[1].rsplit("</message>", 1)[0]
            return CompletionResponse(
                text=json.dumps({"intent": baseline.predict([message])[0].intent, "refund_amount": None})
            )

        @llm_completion_callback()
        def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponseGen:
            raise NotImplementedError

    return StandIn()


def workflow(settings: Settings, erp: OdooErp, *, model: str | None = None, draft: bool = True) -> TriageWorkflow:
    if (model or settings.triage_model) == "fake":
        from llama_index.core import MockEmbedding

        base = load_baseline() if BASELINE.is_file() else train_baseline()
        kb_retriever = index.build_kb(MockEmbedding(embed_dim=8)).as_retriever(similarity_top_k=3)
        fake = stand_in(base)
        return TriageWorkflow(
            base,
            LlmClassifier(fake),
            fake,
            kb_retriever,
            invoice_lookup(erp),
            refund_threshold=settings.refund_approval_threshold,
            draft=draft,
        )
    examples, kb = retrievers(settings)
    the_llm = llm(settings, model)
    return TriageWorkflow(
        load_baseline(),
        LlmClassifier(the_llm, examples),
        the_llm,
        kb,
        invoice_lookup(erp),
        refund_threshold=settings.refund_approval_threshold,
        draft=draft,
    )
