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


def test_number_without_any_citation_is_blocked():
    a = build_answer(_reply("You get 24 days."), _hits(SOURCE), "q")
    assert a.status == UNVERIFIED


def test_list_with_one_marker_passes_when_numbers_are_in_the_cited_source():
    raw = _reply("Leave:\n- Full-time: 24 days\n- Accrual: 2 days per month [1]", [{"source": 1, "quote": REAL_QUOTE}])
    a = build_answer(raw, _hits(SOURCE), "q")
    assert a.status == ANSWERED, a.reason


def test_unmarked_number_not_in_any_cited_source_is_blocked():
    raw = _reply("Leave:\n- Full-time: 30 days\n- Accrual: 2 days per month [1]", [{"source": 1, "quote": REAL_QUOTE}])
    a = build_answer(raw, _hits(SOURCE), "q")
    assert a.status == UNVERIFIED and "30" in a.reason


def test_quote_stitched_with_ellipsis_is_located():
    quote = "Full-time employees get 24 days of paid annual leave ... Leave accrues at 2 days per month."
    assert locate_quote(quote, SOURCE)
    assert locate_quote(quote.replace("...", "\u2026"), SOURCE)


def test_unusual_hyphens_and_spaces_in_quotes_still_match():
    # Groq quoted "pro\u2011rated" (non-breaking hyphen) for a source that says "pro-rated".
    source = "Part-time employees receive a pro-rated amount based on their contracted hours."
    assert locate_quote("part\u2011time employees receive a pro\u2011rated amount based on their contracted hours", source)
    assert locate_quote("Full\u2011time employees get 24\u202fdays of paid annual leave", SOURCE)


def test_stitched_quote_with_an_invented_part_is_rejected():
    assert locate_quote("Full-time employees get 24 days ... Unlimited sick days for everyone.", SOURCE) is None
    assert locate_quote("Full-time employees get 24 days ... Leave accrues at 3 days per month.", SOURCE) is None


@pytest.mark.parametrize("quote", [
    "Notice periods... After probation 4 weeks",
    "Notice periods Situation Notice required (either side) ... Lead, Principal and Director roles 8 weeks",
    "Every new employee has a probation period of 90 days (three months). ... Probation can be extended once, "
    "by up to 60 days, if both of you agree more time is needed.",
])
def test_real_quotes_from_the_eval_are_located_on_page_4(pages, quote):
    # These are quotes Groq and Ollama actually gave; they were wrongly rejected before.
    assert locate_quote(quote, pages[3].text), pages[3].text[:600]


def test_quote_ending_with_ellipsis_is_located():
    assert locate_quote("Full-time employees get 24 days of paid annual leave...", SOURCE)


@pytest.mark.parametrize("page_index, quote", [
    (5, "The studio closes for 10 company holidays: New Year's Day, Martin Luther King Jr. Day, Memorial Day, Juneteenth..."),
    (3, "Lead, Principal and Director roles | Notice required (either side) | After probation | 8 weeks"),
])
def test_more_real_quotes_from_the_eval_are_located(pages, page_index, quote):
    # Ollama gave these with hybrid search on; both were wrongly rejected.
    assert locate_quote(quote, pages[page_index].text), pages[page_index].text[:600]


def test_quote_that_skips_a_sentence_without_ellipsis_is_located(pages):
    # Ollama joined sentences 1 and 3 of the probation paragraph, leaving out the sentence between them.
    quote = ("Every new employee has a probation period of 90 days (three months). Probation can be extended once, "
             "by up to 60 days, if both of you agree more time is needed.")
    assert locate_quote(quote, pages[3].text), pages[3].text[:600]
    assert locate_quote(quote.replace("60 days", "90 days"), pages[3].text) is None  # a changed number still fails


def test_table_row_rebuilt_in_the_models_own_layout_is_located(pages):
    # Ollama mashed a header cell into the row, with no separators.
    assert locate_quote("Lead, Principal and Director roles Notice required (either side) 8 weeks", pages[3].text)


@pytest.mark.parametrize("quote", [
    "Lead, Principal and Director roles Notice required (either side) 12 weeks",   # wrong number
    "Lead, Principal and Director roles get garden leave instead of notice",       # invented words
    "Every new employee has a probation period of 90 days. Probation can never be extended.",  # invented negation
    "Every new employee has a probation period of 90 days. Probation can't be extended.",      # same, contracted
])
def test_rebuilt_quotes_with_wrong_content_are_rejected(pages, quote):
    assert locate_quote(quote, pages[3].text) is None


@pytest.mark.parametrize("page_index, quote", [
    # dropped "not": the source says sick leave does NOT carry over
    (6, "Sick leave does carry over and is paid out when you leave."),
    # added "can't" to an otherwise word-for-word sentence
    (3, "Probation can't be extended once, by up to 60 days, if both of you agree more time is needed."),
])
def test_flipped_negations_are_rejected(pages, page_index, quote):
    assert locate_quote(quote, pages[page_index].text) is None


@pytest.mark.xfail(strict=True, reason="Known limitation: a table value swapped for another value from the same "
                                       "table passes the layout-insensitive check. Needs an LLM faithfulness judge.")
def test_known_limitation_swapped_table_value(pages):
    assert locate_quote("Lead, Principal and Director roles Notice required (either side) 4 weeks", pages[3].text) is None


def test_table_quote_with_an_invented_cell_is_rejected(pages):
    assert locate_quote("Lead, Principal and Director roles | Notice required (either side) | 12 weeks of garden leave",
                        pages[3].text) is None


def test_raw_reply_is_kept_for_diagnostics():
    a = build_answer("not json", _hits(SOURCE), "q")
    assert a.raw == "not json" and a.text == BLOCKED_TEXT


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


def _coverage_reply(coverage, answer="Part-time staff get a pro-rated amount [1].", citations=None):
    return json.dumps({"coverage": coverage, "answer": answer,
                       "citations": citations if citations is not None else [{"source": 1, "quote": REAL_QUOTE}]})


def test_full_coverage_is_a_normal_answer():
    a = build_answer(_coverage_reply("full", "You get 24 days [1]."), _hits(SOURCE), "q")
    assert a.status == ANSWERED and a.found and not a.partial


def test_partial_coverage_is_shown_with_citations_and_flagged():
    a = build_answer(_coverage_reply("partial", "Full-time staff get 24 days [1]; the handbook doesn't give a figure for "
                                                "part-time staff."), _hits(SOURCE), "q")
    assert a.status == ANSWERED and a.found and a.partial and a.citations


def test_partial_coverage_still_needs_valid_evidence():
    a = build_answer(_coverage_reply("partial", "Part-time staff get 30 days [1]."), _hits(SOURCE), "q")
    assert a.status == UNVERIFIED  # 30 is not in the source


def test_no_coverage_is_not_found():
    a = build_answer(_coverage_reply("none", "The documents don't cover this.", []), _hits(SOURCE), "q")
    assert a.status == NOT_FOUND and not a.found


def test_unknown_coverage_value_is_invalid():
    assert build_answer(_coverage_reply("mostly"), _hits(SOURCE), "q").status == INVALID_OUTPUT


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
