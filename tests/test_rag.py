"""Tests that run without API keys, using the offline embedder and chat stand-ins."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from rag.citations import build_answer, locate_quote, parse_model_json
from rag.documents import Chunk, Page, PdfError, chunk_pages, load_pdf
from rag.offline import HashingEmbedder, KeywordChat
from rag.pipeline import NOT_FOUND_TEXT, RAGPipeline
from rag.store import Hit, VectorStore

PDF = Path(__file__).resolve().parent.parent / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"


@pytest.fixture(scope="module")
def pages():
    return load_pdf(PDF.read_bytes(), PDF.name)


@pytest.fixture(scope="module")
def store(pages):
    s = VectorStore(HashingEmbedder(), "test")
    s.add(chunk_pages(pages))
    return s


# ---- loading and chunking ------------------------------------------------------------------

def test_loads_every_page_with_numbers(pages):
    assert [p.page for p in pages] == list(range(1, 17))
    assert "24 days of paid annual leave" in pages[5].text


def test_repeated_footer_is_removed(pages):
    assert not any("Sample document for demo purposes" in p.text for p in pages)


def test_chunks_stay_on_one_page_and_respect_size(pages):
    chunks = chunk_pages(pages, chunk_chars=400)
    assert len({c.id for c in chunks}) == len(chunks)
    for c in chunks:
        assert c.text in " ".join(p.text for p in pages if p.page == c.page) or len(c.text) <= 600
        assert len(c.text) <= 600


def test_long_text_without_punctuation_is_split():
    page = Page("x.pdf", 1, "word " * 1000)
    chunks = chunk_pages([page], chunk_chars=300)
    assert len(chunks) > 5
    assert all(len(c.text) <= 450 for c in chunks)


def test_rejects_non_pdf():
    with pytest.raises(PdfError):
        load_pdf(b"not a pdf", "fake.pdf")


def test_rejects_pdf_without_text():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(PdfError, match="scanned"):
        load_pdf(buf.getvalue(), "blank.pdf")


# ---- citations -----------------------------------------------------------------------------

SOURCE = "Full-time employees get 24 days of paid annual leave each calendar year. Leave accrues at 2 days per month."


def _hits(*texts, page=6):
    return [Hit(Chunk(f"c{i}", "h.pdf", page + i, t), 0.9) for i, t in enumerate(texts)]


def test_parse_json_with_code_fence():
    assert parse_model_json('```json\n{"found": true, "answer": "x"}\n```') == {"found": True, "answer": "x"}
    assert parse_model_json("no json here") is None


def test_exact_quote_is_verified():
    assert locate_quote("Full-time employees get 24 days of paid annual leave each calendar year.", SOURCE)


def test_quote_with_curly_quotes_and_small_changes_is_verified():
    assert locate_quote("Full time employees get 24 days of paid annual leave each calendar year", SOURCE)


def test_made_up_quote_is_rejected():
    assert locate_quote("Employees get unlimited paid vacation.", SOURCE) is None


def test_answer_keeps_page_and_flags_unverified_quotes():
    raw = json.dumps({
        "found": True,
        "answer": "You get 24 days [1]. Unlimited sick days [2].",
        "citations": [{"source": 1, "quote": "Full-time employees get 24 days of paid annual leave each calendar year."},
                      {"source": 2, "quote": "Unlimited sick days for everyone."}],
    })
    a = build_answer(raw, _hits(SOURCE, "Every employee gets 10 days of paid sick leave per calendar year."), "q")
    assert a.found
    assert [(c.number, c.page, c.verified) for c in a.citations] == [(1, 6, True), (2, 7, False)]
    # An unverified quote is replaced by the source's own text, never shown as the model wrote it.
    assert "Unlimited" not in a.citations[1].quote


def test_markers_for_missing_sources_are_dropped():
    raw = json.dumps({"found": True, "answer": "Fact [1]. Other fact [7].", "citations": []})
    a = build_answer(raw, _hits(SOURCE), "q")
    assert "[7]" not in a.text and [c.number for c in a.citations] == [1]


def test_not_found_has_no_citations():
    raw = json.dumps({"found": False, "answer": "The documents don't cover this.", "citations": [{"source": 1, "quote": "x"}]})
    a = build_answer(raw, _hits(SOURCE), "q")
    assert not a.found and a.citations == []


def test_plain_text_reply_is_still_usable():
    a = build_answer("You get 24 days [1].", _hits(SOURCE), "q")
    assert a.found and a.citations[0].page == 6


# ---- end to end with offline providers ----------------------------------------------------

@pytest.mark.parametrize(
    "question, page",
    [
        ("How many days of annual leave do I get?", 6),
        ("How long is the probation period?", 4),
        ("How many paid sick days do I get?", 7),
        ("What is my yearly learning budget?", 11),
    ],
)
def test_answers_cite_the_right_page(store, question, page):
    a = RAGPipeline(store, KeywordChat()).ask(question)
    assert a.found
    assert page in [c.page for c in a.citations]
    assert all(c.verified for c in a.citations)


def test_question_outside_the_documents_is_refused(store):
    a = RAGPipeline(store, KeywordChat()).ask("What is the dress code for client meetings?")
    assert not a.found and a.citations == []


def test_empty_store_returns_not_found():
    a = RAGPipeline(VectorStore(HashingEmbedder(), "empty"), KeywordChat()).ask("anything")
    assert not a.found and a.text == NOT_FOUND_TEXT


def test_follow_up_is_rewritten_before_search(store):
    class Recorder(KeywordChat):
        def __init__(self):
            super().__init__()
            self.calls = []

        def complete(self, system, messages, max_tokens=900):
            self.calls.append(messages[-1]["content"])
            if "Sources:" not in messages[-1]["content"]:
                return "How many paid sick days do part-time employees get?"
            return super().complete(system, messages, max_tokens)

    chat = Recorder()
    history = [{"role": "user", "content": "How many sick days do I get?"},
               {"role": "assistant", "content": "10 days [1]."}]
    a = RAGPipeline(store, chat).ask("And for part-time staff?", history)
    assert a.search_query == "How many paid sick days do part-time employees get?"
    assert "Latest question: And for part-time staff?" in chat.calls[0]
