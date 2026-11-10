"""Search over document chunks: dense (Chroma embeddings), keyword (BM25), or both fused (hybrid)."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

import chromadb

from .documents import Chunk
from .lexical import BM25, reciprocal_rank_fusion
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
    score: float  # higher is closer: cosine similarity (dense), BM25 score (bm25) or RRF score (hybrid)


SEARCH_MODES = ("dense", "bm25", "hybrid")


class VectorStore:
    def __init__(self, embedder: Embedder, name: str | None = None):
        self.embedder = embedder
        safe = re.sub(r"[^a-zA-Z0-9._-]", "-", name or "kb")[:40].strip("-._") or "kb"
        self.collection_name = f"{safe}-{uuid.uuid4().hex[:8]}"
        self._col = _chroma().create_collection(
            self.collection_name, configuration={"hnsw": {"space": "cosine"}}, embedding_function=None
        )
        self.sources: dict[str, int] = {}  # source name -> page count indexed
        self._chunks: list[Chunk] = []  # same chunks as in Chroma, kept for keyword search
        self._bm25: BM25 | None = None  # built on first keyword search, rebuilt after add()

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
        self._chunks.extend(chunks)
        self._bm25 = None

    def __len__(self) -> int:
        return self._col.count()

    def search(self, query: str, k: int = 5, mode: str = "dense") -> list[Hit]:
        if mode not in SEARCH_MODES:
            raise ValueError(f"Unknown search mode {mode!r}; use one of {SEARCH_MODES}")
        if len(self) == 0:
            return []
        if mode == "dense":
            return self._dense(query, k)
        if mode == "bm25":
            return self._keyword(query, k)
        # hybrid: take a wider pool from each method, fuse by rank, keep the best k
        pool = min(len(self), max(4 * k, 20))
        dense, keyword = self._dense(query, pool), self._keyword(query, pool)
        by_id = {h.chunk.id: h.chunk for h in dense + keyword}
        fused = reciprocal_rank_fusion([[h.chunk.id for h in dense], [h.chunk.id for h in keyword]])
        return [Hit(by_id[cid], score) for cid, score in fused[:k]]

    def _keyword(self, query: str, k: int) -> list[Hit]:
        if self._bm25 is None:
            self._bm25 = BM25([c.text for c in self._chunks])
        return [Hit(self._chunks[i], s) for i, s in self._bm25.top(query, k)]

    def _dense(self, query: str, k: int) -> list[Hit]:
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
