"""Reranking: the pipeline retrieves a wider pool, reorders it, keeps the best top_k, and never breaks on failure."""
from __future__ import annotations

import pytest

from rag.documents import Chunk
from rag.offline import KeywordChat
from rag.pipeline import RAGPipeline
from rag.providers import ProviderError
from rag.rerank import CrossEncoderReranker, make_reranker
from rag.store import Hit


def _hits(n):
    return [Hit(Chunk(f"c{i}", "h.pdf", i + 1, f"passage {i}"), 1.0 - i / 100) for i in range(n)]


class FakeStore:
    def __init__(self, n=20):
        self.n, self.calls = n, []

    def search(self, query, k=5, mode="dense"):
        self.calls.append((k, mode))
        return _hits(min(k, self.n))


class Reverse:
    name = "reverse"

    def rerank(self, query, hits):
        return [Hit(h.chunk, -i) for i, h in enumerate(reversed(hits))]  # negative scores, like real cross-encoders


class Broken:
    name = "broken"

    def rerank(self, query, hits):
        raise RuntimeError("model crashed")


def test_reranker_sees_a_wider_pool_and_top_k_are_kept():
    store = FakeStore()
    hits = RAGPipeline(store, KeywordChat(), top_k=5, reranker=Reverse(), rerank_pool=20).retrieve("q")
    assert store.calls == [(20, "hybrid")]
    assert [h.chunk.id for h in hits] == ["c19", "c18", "c17", "c16", "c15"]


def test_negative_rerank_scores_are_not_filtered_out():
    hits = RAGPipeline(FakeStore(), KeywordChat(), top_k=3, min_score=0.0, reranker=Reverse()).retrieve("q")
    assert len(hits) == 3 and all(h.score <= 0 for h in hits)


def test_without_a_reranker_only_top_k_are_retrieved():
    store = FakeStore()
    assert len(RAGPipeline(store, KeywordChat(), top_k=5).retrieve("q")) == 5
    assert store.calls == [(5, "hybrid")]


def test_a_failing_reranker_falls_back_to_retrieval_order():
    pipeline = RAGPipeline(FakeStore(), KeywordChat(), top_k=3, reranker=Broken())
    assert [h.chunk.id for h in pipeline.retrieve("q")] == ["c0", "c1", "c2"]
    assert "model crashed" in pipeline.rerank_error


def test_cross_encoder_orders_by_its_scores():
    class FakeEncoder:
        def rerank(self, query, documents):
            return [len(d) for d in documents]  # longer passage = more relevant, for the test

    hits = [Hit(Chunk("a", "h.pdf", 1, "short"), 0.9), Hit(Chunk("b", "h.pdf", 2, "a much longer passage"), 0.5)]
    out = CrossEncoderReranker(encoder=FakeEncoder()).rerank("q", hits)
    assert [h.chunk.id for h in out] == ["b", "a"] and out[0].score == len("a much longer passage")


def test_make_reranker_names():
    assert make_reranker("none") is None and make_reranker("") is None
    with pytest.raises(ProviderError, match="Unknown reranker"):
        make_reranker("magic")
