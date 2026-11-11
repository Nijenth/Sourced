"""Workspace-scoped Postgres retrieval for the Sourced pipeline."""

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from rag.documents import Chunk
from rag.lexical import BM25, reciprocal_rank_fusion
from rag.providers import Embedder
from rag.store import SEARCH_MODES, Hit

from .db.models import Document, DocumentChunk

MAX_BM25_CHUNKS = 10_000


class KeywordSearchLimit(ValueError):
    pass


@dataclass(frozen=True)
class DocumentHit(Hit):
    document_id: UUID


class PgVectorStore:
    def __init__(
        self, sessions: sessionmaker, embedder: Embedder, workspace_id: UUID,
        document_ids: set[UUID] | None = None,
    ):
        self.sessions = sessions
        self.embedder = embedder
        self.workspace_id = workspace_id
        self.document_ids = document_ids

    def _scope(self):
        scope = (
            select(
                DocumentChunk.id, DocumentChunk.document_id, Document.filename, DocumentChunk.page,
                DocumentChunk.ordinal, DocumentChunk.text,
            )
            .join(Document, Document.id == DocumentChunk.document_id)
            .where(
                Document.workspace_id == self.workspace_id, Document.status == "ready",
                Document.embedding_model == self.embedder.name,
            )
        )
        if self.document_ids is not None:
            scope = scope.where(Document.id.in_(self.document_ids))
        return scope

    @staticmethod
    def _hit(row, score: float = 0.0) -> DocumentHit:
        return DocumentHit(Chunk(str(row.id), row.filename, row.page, row.text), score, row.document_id)

    def _dense(self, db, scope, query: str, k: int) -> list[DocumentHit]:
        embed_query = getattr(self.embedder, "embed_query", None)
        vector = embed_query(query) if embed_query else self.embedder.embed([query])[0]
        # Filter before evaluating distance, including when two model dimensions coexist during reindexing.
        candidates = (
            scope.add_columns(DocumentChunk.embedding)
            .where(func.vector_dims(DocumentChunk.embedding) == len(vector))
            .cte("allowed_chunks")
            .prefix_with("MATERIALIZED")
        )
        distance = func.coalesce(func.nullif(candidates.c.embedding.cosine_distance(vector), float("nan")), 1.0)
        rows = db.execute(
            select(candidates, distance.label("distance"))
            .order_by(distance, candidates.c.document_id, candidates.c.ordinal)
            .limit(k)
        )
        return [self._hit(row, 1.0 - row.distance) for row in rows]

    def search(self, query: str, k: int = 5, mode: str = "dense") -> list[Hit]:
        if mode not in SEARCH_MODES:
            raise ValueError(f"Unknown search mode {mode!r}; use one of {SEARCH_MODES}")
        if k <= 0 or (self.document_ids is not None and not self.document_ids):
            return []
        scope = self._scope()
        with self.sessions() as db:
            db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
            if mode == "dense":
                if db.scalar(scope.with_only_columns(DocumentChunk.id).limit(1)) is None:
                    return []
                return self._dense(db, scope, query, k)

            rows = db.execute(
                scope.order_by(DocumentChunk.document_id, DocumentChunk.ordinal).limit(MAX_BM25_CHUNKS + 1)
            ).all()
            if not rows:
                return []
            if len(rows) > MAX_BM25_CHUNKS:
                raise KeywordSearchLimit("Select fewer documents for keyword or hybrid search.")
            pool = k if mode == "bm25" else min(len(rows), max(4 * k, 20))
            bm25 = BM25([row.text for row in rows])
            keyword = [self._hit(rows[i], score) for i, score in bm25.top(query, pool)]
            if mode == "bm25":
                return keyword

            dense = self._dense(db, scope, query, pool)
            by_id = {hit.chunk.id: hit for hit in dense + keyword}
            fused = reciprocal_rank_fusion([[hit.chunk.id for hit in ranking] for ranking in (dense, keyword)])
            return [replace(by_id[chunk_id], score=score) for chunk_id, score in fused[:k]]
