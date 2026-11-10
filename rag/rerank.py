"""Rerankers: reorder retrieved passages by reading the question and each passage together.

Retrieval (dense, BM25 or hybrid) scores the question and each passage separately, which is fast but
coarse. A cross-encoder reads them as a pair, which is slower but more precise. So the pipeline
retrieves a wider pool (e.g. 20), reranks it, and keeps the best few for the answer.

Like the chat and embedding providers, rerankers share one small interface, so others (an API
reranker, or a chat model used as a reranker) can be added in their own class later.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from .providers.base import ProviderError, import_sdk
from .store import Hit

DEFAULT_CROSS_ENCODER = "Xenova/ms-marco-MiniLM-L-6-v2"  # ~80 MB, English, runs on CPU via onnxruntime
RERANKERS = ("none", "cross-encoder")


class Reranker(Protocol):
    name: str

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]: ...


class CrossEncoderReranker:
    """A local cross-encoder through fastembed. The model downloads once (~80 MB) on first use."""

    def __init__(self, model: str = DEFAULT_CROSS_ENCODER, encoder=None):
        if encoder is None:
            cross = import_sdk("fastembed.rerank.cross_encoder", "fastembed", "The cross-encoder reranker")
            try:
                encoder = cross.TextCrossEncoder(model_name=model)
            except Exception as exc:  # download or model-name problems
                raise ProviderError(f"Couldn't load the reranker model '{model}'. Check the name and your connection.") from exc
        self._encoder = encoder
        self.model = model
        self.name = f"cross-encoder:{model}"

    def rerank(self, query: str, hits: list[Hit]) -> list[Hit]:
        if not hits:
            return hits
        scores = list(self._encoder.rerank(query, [h.chunk.text for h in hits]))
        ranked = sorted(zip(hits, scores), key=lambda pair: -pair[1])
        return [replace(hit, score=float(score)) for hit, score in ranked]


def make_reranker(name: str, model: str = "") -> Reranker | None:
    """Build the reranker named in settings, or None for "none"."""
    name = (name or "none").strip().lower()
    if name == "none":
        return None
    if name == "cross-encoder":
        return CrossEncoderReranker(model or DEFAULT_CROSS_ENCODER)
    raise ProviderError(f"Unknown reranker '{name}'. Use one of: {', '.join(RERANKERS)}.")
