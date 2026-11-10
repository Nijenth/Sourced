"""A thin wrapper over an in-memory Chroma collection."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import chromadb

from .documents import Chunk
from .providers import Embedder

_client = None


def _chroma():
    global _client
    if _client is None:
        _client = chromadb.EphemeralClient()
    return _client


@dataclass(frozen=True)
class Hit:
    chunk: Chunk
    score: float  # cosine similarity, higher is closer


class VectorStore:
    def __init__(self, embedder: Embedder, name: str | None = None):
        self.embedder = embedder
        safe = re.sub(r"[^a-zA-Z0-9._-]", "-", name or "kb")[:40].strip("-._") or "kb"
        self.collection_name = f"{safe}-{uuid.uuid4().hex[:8]}"
        self._col = _chroma().create_collection(
            self.collection_name, configuration={"hnsw": {"space": "cosine"}}, embedding_function=None
        )
        self.sources: dict[str, int] = {}  # source name -> page count indexed

    def add(self, chunks: list[Chunk]) -> None:
        if not chunks:
            return
        vectors = self.embedder.embed([c.text for c in chunks])
        self._col.add(
            ids=[c.id for c in chunks],
            embeddings=vectors,
            documents=[c.text for c in chunks],
            metadatas=[{"source": c.source, "page": c.page} for c in chunks],
        )
        for c in chunks:
            self.sources[c.source] = max(self.sources.get(c.source, 0), c.page)

    def __len__(self) -> int:
        return self._col.count()

    def search(self, query: str, k: int = 5) -> list[Hit]:
        if len(self) == 0:
            return []
        embed_query = getattr(self.embedder, "embed_query", None)
        qv = embed_query(query) if embed_query else self.embedder.embed([query])[0]
        res = self._col.query(
            query_embeddings=[qv], n_results=min(k, len(self)), include=["documents", "metadatas", "distances"]
        )
        hits = []
        for cid, doc, meta, dist in zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(Hit(Chunk(cid, str(meta["source"]), int(meta["page"]), doc), 1.0 - float(dist)))
        return hits

    def drop(self) -> None:
        try:
            _chroma().delete_collection(self.collection_name)
        except Exception:
            pass
