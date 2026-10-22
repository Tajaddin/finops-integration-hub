"""The two vector indexes, both LlamaIndex over pgvector in the hub's database:

- `triage_examples`: the labelled training messages, for retrieval few-shot classification;
- `triage_kb`: the help-centre articles (kb/articles.md), one node per article, for cited replies.

Embeddings come from a local model through Ollama (nomic-embed-text, 768 dimensions). Tests use an in-memory store.
"""

from __future__ import annotations

import re
from importlib import resources
from urllib.parse import urlparse

from llama_index.core import StorageContext, VectorStoreIndex
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.schema import TextNode
from llama_index.core.vector_stores.types import BasePydanticVectorStore

from hub.triage.data import Message

EMBED_DIM = 768


def kb_articles() -> dict[str, tuple[str, str]]:
    """{article id: (title, text)} parsed from the packaged markdown."""
    text = resources.files("hub.triage").joinpath("kb/articles.md").read_text(encoding="utf-8")
    out = {}
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        head, _, body = block.partition("\n")
        kb_id, _, title = head.partition(" ")
        out[kb_id.strip()] = (title.strip(), " ".join(body.split()))
    return out


def pg_store(dsn: str, table: str, embed_dim: int = EMBED_DIM) -> BasePydanticVectorStore:
    from llama_index.vector_stores.postgres import PGVectorStore

    u = urlparse(dsn)
    return PGVectorStore.from_params(
        host=u.hostname,
        port=str(u.port or 5432),
        database=u.path.lstrip("/"),
        user=u.username,
        password=u.password,
        table_name=table,
        embed_dim=embed_dim,
        hnsw_kwargs={
            "hnsw_m": 16,
            "hnsw_ef_construction": 64,
            "hnsw_ef_search": 40,
            "hnsw_dist_method": "vector_cosine_ops",
        },
    )


def build_examples(
    train: list[Message], embed: BaseEmbedding, store: BasePydanticVectorStore | None = None
) -> VectorStoreIndex:
    nodes = [TextNode(text=m.text, id_=m.id, metadata={"intent": m.intent, "category": m.category}) for m in train]
    ctx = StorageContext.from_defaults(vector_store=store) if store is not None else None
    return VectorStoreIndex(nodes, storage_context=ctx, embed_model=embed, show_progress=False, insert_batch_size=512)


def build_kb(embed: BaseEmbedding, store: BasePydanticVectorStore | None = None) -> VectorStoreIndex:
    nodes = [
        TextNode(text=f"{title}. {body}", id_=kb_id, metadata={"kb_id": kb_id, "title": title})
        for kb_id, (title, body) in kb_articles().items()
    ]
    ctx = StorageContext.from_defaults(vector_store=store) if store is not None else None
    return VectorStoreIndex(nodes, storage_context=ctx, embed_model=embed, show_progress=False)


def load(store: BasePydanticVectorStore, embed: BaseEmbedding) -> VectorStoreIndex:
    return VectorStoreIndex.from_vector_store(store, embed_model=embed)
