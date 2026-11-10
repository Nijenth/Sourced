"""Tests that run without API keys, using the offline embedder and chat stand-ins."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from rag.citations import (
    ANSWERED,
    BLOCKED_TEXT,
    INVALID_OUTPUT,
    NOT_FOUND,
    UNVERIFIED,
    build_answer,
    locate_quote,
    numbers_in,
    parse_model_json,
)
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


REAL_QUOTE = "Full-time employees get 24 days of paid annual leave each calendar year."


def _reply(answer, citations=None, found=True):
    return json.dumps({"found": found, "answer": answer, "citations": citations or []})


def test_quote_with_changed_digit_is_rejected():
    assert locate_quote("Full-time employees get 42 days of paid annual leave each calendar year.", SOURCE) is None


def test_numbers_are_normalised():
    assert numbers_in("1,000 rupees, 09:30, twenty-four days, 2.50") >= {"1000", "9", "30", "24", "2.5"}
    assert "1" not in numbers_in("one of the options", ignore_one=True)


def test_valid_answer_passes():
    raw = _reply("Full-time staff get 24 days of annual leave [1]. Leave builds up at two days a month [1].",
                 [{"source": 1, "quote": REAL_QUOTE}])
    a = build_answer(raw, _hits(SOURCE), "q")
    assert a.found and a.status == ANSWERED
    assert [(c.number, c.page, c.verified) for c in a.citations] == [(1, 6, True)]


def test_marker_after_full_stop_counts():
    a = build_answer(_reply("You get 24 days. [1]", [{"source": 1, "quote": REAL_QUOTE}]), _hits(SOURCE), "q")
    assert a.status == ANSWERED


def test_sentence_without_numbers_needs_no_marker():
    raw = _reply("You get 24 days [1]. The handbook doesn't mention contractors.", [{"source": 1, "quote": REAL_QUOTE}])
    assert build_answer(raw, _hits(SOURCE), "q").status == ANSWERED


def test_real_quote_with_wrong_number_in_answer_is_blocked():
    # The citation is genuine, but the claim beside it is false.
    a = build_answer(_reply("You get 42 days [1].", [{"source": 1, "quote": REAL_QUOTE}]), _hits(SOURCE), "q")
    assert not a.found and a.status == UNVERIFIED and a.citations == []
    assert a.text == BLOCKED_TEXT and "42" in a.reason


def test_fabricated_quote_blocks_the_whole_answer():
    raw = _reply("You get 24 days [1]. Unlimited sick days [2].",
                 [{"source": 1, "quote": REAL_QUOTE}, {"source": 2, "quote": "Unlimited sick days for everyone."}])
    a = build_answer(raw, _hits(SOURCE, "Every employee gets 10 days of paid sick leave per calendar year."), "q")
    assert not a.found and a.status == UNVERIFIED and a.citations == []
    assert len(a.hits) == 2  # the passages are kept so the user can still read them


def test_number_without_citation_is_blocked():
    a = build_answer(_reply("You get 24 days.", [{"source": 1, "quote": REAL_QUOTE}]), _hits(SOURCE), "q")
    assert a.status == UNVERIFIED


def test_marker_for_missing_source_is_blocked():
    a = build_answer(_reply("Fact [1]. Other fact [7]."), _hits(SOURCE), "q")
    assert a.status == UNVERIFIED and "7" in a.reason


def test_answer_without_any_citation_is_blocked():
    assert build_answer(_reply("Leave is generous."), _hits(SOURCE), "q").status == UNVERIFIED


def test_not_found_has_no_citations():
    a = build_answer(_reply("The documents don't cover this.", [{"source": 1, "quote": "x"}], found=False), _hits(SOURCE), "q")
    assert not a.found and a.citations == [] and a.status == NOT_FOUND


def test_plain_text_reply_is_rejected():
    a = build_answer("You get 24 days [1].", _hits(SOURCE), "q")
    assert not a.found and a.status == INVALID_OUTPUT


def test_found_must_be_a_real_boolean():
    raw = json.dumps({"found": "false", "answer": "You get 24 days [1].", "citations": []})
    assert build_answer(raw, _hits(SOURCE), "q").status == INVALID_OUTPUT


def test_citations_of_the_wrong_type_do_not_crash():
    raw = json.dumps({"found": True, "answer": "You get 24 days [1].", "citations": 5})
    assert build_answer(raw, _hits(SOURCE), "q").status == INVALID_OUTPUT


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


# ---- retry on malformed replies --------------------------------------------------------------

class _Scripted:
    """Chat stand-in that returns prepared replies in order."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def complete(self, system, messages, max_tokens=900):
        self.calls += 1
        return self.replies.pop(0)


def test_malformed_reply_is_retried_once(store):
    good = _reply("Full-time employees get 24 days of paid annual leave [1].", [{"source": 1, "quote": REAL_QUOTE}])
    chat = _Scripted("not json at all", good)
    a = RAGPipeline(store, chat).ask("How many days of annual leave do I get?")
    # The good reply only passes if source [1] is the annual-leave page, which retrieval puts first.
    assert chat.calls == 2 and a.attempts == 2
    assert a.status != INVALID_OUTPUT


def test_second_malformed_reply_is_blocked(store):
    chat = _Scripted("not json", "still not json")
    a = RAGPipeline(store, chat).ask("How many days of annual leave do I get?")
    assert chat.calls == 2 and a.status == INVALID_OUTPUT and not a.found


def test_evidence_failures_are_not_retried(store):
    chat = _Scripted(_reply("You get 999 days [1]."))
    a = RAGPipeline(store, chat).ask("How many days of annual leave do I get?")
    assert chat.calls == 1 and a.status == UNVERIFIED
