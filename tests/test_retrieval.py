"""Keyword search (BM25), rank fusion (RRF) and the three search modes."""
from __future__ import annotations

from pathlib import Path

import pytest

from rag.documents import chunk_pages, load_pdf
from rag.lexical import BM25, reciprocal_rank_fusion, tokenize
from rag.offline import HashingEmbedder, KeywordChat
from rag.pipeline import RAGPipeline
from rag.store import VectorStore

PDF = Path(__file__).resolve().parent.parent / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"


def test_tokenize_drops_filler_words_and_lightly_stems():
    assert tokenize("How much notice must I give when resigning?") == ["notice", "must", "give", "resign"]
    assert tokenize("4 weeks") == ["4", "week"]
    assert tokenize("Leave and leaves") == ["leave", "leave"]


def test_bm25_ranks_the_document_with_the_rare_word_first():
    docs = ["Annual leave is 24 days.", "Notice: Lead, Principal and Director roles give 8 weeks.", "Resign by email."]
    bm25 = BM25(docs)
    assert bm25.top("director notice", 3)[0][0] == 1
    assert all(i != 0 for i, _ in bm25.top("director", 3))  # no shared words, so not returned at all


def test_rrf_rewards_items_both_lists_agree_on():
    fused = [item for item, _ in reciprocal_rank_fusion([["a", "b", "c"], ["c", "d", "b"]])]
    assert fused[0] in ("b", "c") and set(fused[:2]) == {"b", "c"}
    assert fused.index("b") < fused.index("d")


@pytest.fixture(scope="module")
def store():
    s = VectorStore(HashingEmbedder(), "retrieval-test")
    s.add(chunk_pages(load_pdf(PDF.read_bytes(), PDF.name)))
    return s


DIRECTOR_Q = ("I'm a Director and I'm resigning. How much notice must I give, "
              "and how soon must I return my laptop after my last day?")


@pytest.mark.parametrize("mode", ["bm25", "hybrid"])
def test_keyword_aware_modes_find_the_notice_table_for_a_director(store, mode):
    # Dense search alone missed page 4 for this question in the Groq and Ollama runs.
    pages = [h.chunk.page for h in store.search(DIRECTOR_Q, 5, mode=mode)]
    assert 4 in pages, pages


def test_every_mode_returns_at_most_k_unique_chunks(store):
    for mode in ("dense", "bm25", "hybrid"):
        hits = store.search("annual leave carry over", 5, mode=mode)
        assert 0 < len(hits) <= 5
        assert len({h.chunk.id for h in hits}) == len(hits)


def test_unknown_mode_is_rejected(store):
    with pytest.raises(ValueError):
        store.search("anything", 5, mode="magic")


def test_pipeline_uses_its_retrieval_mode(store):
    seen = []

    class Spy(VectorStore):
        def __init__(self):  # reuse the real store's data
            self.__dict__.update(store.__dict__)

        def search(self, query, k=5, mode="dense"):
            seen.append(mode)
            return super().search(query, k, mode=mode)

    RAGPipeline(Spy(), KeywordChat(), retrieval_mode="bm25").ask("How many paid sick days do I get?")
    assert seen == ["bm25"]
