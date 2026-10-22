"""LLM traces to the self-hosted Langfuse: the Langfuse SDK (OpenTelemetry underneath) plus OpenInference's
instrumentation of LlamaIndex, so every triage turn is one trace with the retrieval, the LLM calls (prompt,
completion, token counts) and the workflow steps as child spans. Off unless Langfuse keys are configured.

Messages are customer data: they go only to the Langfuse in this stack, and the sender is recorded as a hash.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from hub import __version__, log
from hub.config import Settings

logger = log.get(__name__)
_client: Any = None


def setup(settings: Settings) -> Any:
    """The Langfuse client, created once; None when tracing is not configured."""
    global _client  # noqa: PLW0603  (one client per process, like the OTel provider it owns)
    if _client is not None:
        return _client
    public, secret = os.environ.get("LANGFUSE_PUBLIC_KEY", ""), os.environ.get("LANGFUSE_SECRET_KEY", "")
    if not public or not secret or "generate-me" in public:
        return None
    from langfuse import Langfuse
    from openinference.instrumentation.llama_index import LlamaIndexInstrumentor

    _client = Langfuse(
        public_key=public, secret_key=secret, base_url=settings.langfuse_url, environment="local", release=__version__
    )
    LlamaIndexInstrumentor().instrument()
    logger.info("tracing_enabled", langfuse=settings.langfuse_url)
    return _client


def sender_id(email: str) -> str:
    return "sender-" + hashlib.sha256(email.lower().encode()).hexdigest()[:12]
