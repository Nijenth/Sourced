"""The accuracy judge: verdict parsing, what it is shown, how the pipeline uses it, and the tamper helper."""
from __future__ import annotations

import json

from rag.citations import ANSWERED, NOT_FOUND, UNVERIFIED, build_answer
from rag.documents import Chunk
from rag.judge import Judge, tamper_number
from rag.pipeline import RAGPipeline
from rag.providers import ProviderError
from rag.store import Hit

LEAVE = "Full-time employees get 24 days of paid annual leave each calendar year. Leave accrues at 2 days per month."


class Scripted:
    """A chat stand-in that returns prepared replies and records what it was asked."""

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []
        self.name = self.label = "scripted"

    def complete(self, system, messages, max_tokens=900):
        self.prompts.append(messages[-1]["content"])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _answer(text="You get 24 days [1].", quote="Full-time employees get 24 days of paid annual leave each calendar year."):
    hits = [Hit(Chunk("c1", "handbook.pdf", 6, LEAVE), 1.0)]
    raw = json.dumps({"coverage": "full", "answer": text, "citations": [{"source": 1, "quote": quote}]})
    return build_answer(raw, hits, "How much leave do I get?")


def _verdict(verdict, claims=()):
    return json.dumps({"verdict": verdict, "unsupported_claims": list(claims)})


def test_supported_verdict_and_the_judge_sees_the_cited_passage():
    judge_chat = Scripted(_verdict("supported"))
    j = Judge(judge_chat).check("How much leave?", _answer())
    assert j.verdict == "supported" and j.score == 1.0 and j.problems == []
    assert LEAVE in judge_chat.prompts[0] and "You get 24 days" in judge_chat.prompts[0]


def test_judge_sees_every_retrieved_passage_and_records_its_name():
    other = "Notice periods: Lead, Principal and Director roles give 8 weeks."
    hits = [Hit(Chunk("c1", "handbook.pdf", 6, LEAVE), 1.0), Hit(Chunk("c2", "handbook.pdf", 4, other), 0.9)]
    raw = json.dumps({"coverage": "full", "answer": "You get 24 days [1].",
                      "citations": [{"source": 1, "quote": "Full-time employees get 24 days of paid annual leave"}]})
    judge_chat = Scripted(_verdict("supported"))
    j = Judge(judge_chat).check("q", build_answer(raw, hits, "q"))
    assert other in judge_chat.prompts[0]  # uncited passage [2] is shown too, to catch false "not covered"
    assert "[2] handbook.pdf, page 4" in judge_chat.prompts[0]
    assert j.judge == "scripted"


def test_unsupported_verdict_lists_the_claims():
    j = Judge(Scripted(_verdict("unsupported", ["4 weeks of notice"]))).check("q", _answer())
    assert j.verdict == "unsupported" and j.score == 0.0 and j.problems == ["4 weeks of notice"]


def test_unreadable_reply_or_provider_failure_is_an_error_not_a_crash():
    assert Judge(Scripted("I think it's fine")).check("q", _answer()).verdict == "error"
    assert Judge(Scripted(_verdict("maybe"))).check("q", _answer()).verdict == "error"
    j = Judge(Scripted(ProviderError("rate limited"))).check("q", _answer())
    assert j.verdict == "error" and j.score is None


class OneHitStore:
    def search(self, query, k=5, mode="dense"):
        return [Hit(Chunk("c1", "handbook.pdf", 6, LEAVE), 1.0)]


ANSWER_JSON = json.dumps({"coverage": "full", "answer": "You get 24 days [1].",
                          "citations": [{"source": 1, "quote": "Full-time employees get 24 days of paid annual leave"}]})


def test_pipeline_warn_mode_keeps_the_answer_and_attaches_the_verdict():
    p = RAGPipeline(OneHitStore(), Scripted(ANSWER_JSON), judge=Judge(Scripted(_verdict("unsupported", ["x"]))))
    a = p.ask("How much leave do I get?")
    assert a.status == ANSWERED and a.judgement.verdict == "unsupported"


def test_pipeline_block_mode_hides_unsupported_answers():
    p = RAGPipeline(OneHitStore(), Scripted(ANSWER_JSON), judge=Judge(Scripted(_verdict("unsupported", ["24 days"]))),
                    judge_action="block")
    a = p.ask("How much leave do I get?")
    assert a.status == UNVERIFIED and not a.found and "24 days" in a.reason and a.citations == []


def test_pipeline_block_mode_keeps_supported_answers():
    p = RAGPipeline(OneHitStore(), Scripted(ANSWER_JSON), judge=Judge(Scripted(_verdict("supported"))), judge_action="block")
    assert p.ask("How much leave do I get?").status == ANSWERED


def test_judge_is_not_called_for_not_found_answers():
    not_found = json.dumps({"coverage": "none", "answer": "The documents don't cover this.", "citations": []})
    judge_chat = Scripted()  # would raise IndexError if called
    a = RAGPipeline(OneHitStore(), Scripted(not_found), judge=Judge(judge_chat)).ask("Dress code?")
    assert a.status == NOT_FOUND and a.judgement is None and judge_chat.prompts == []


def test_tamper_swaps_a_number_for_another_from_the_evidence_and_skips_markers():
    assert tamper_number("You get 24 days [1].", LEAVE) == "You get 2 days [1]."
    assert tamper_number("See the policy [1].", LEAVE) is None
    assert tamper_number("You get 24 days [1].", "no numbers here") == "You get 25 days [1]."
    # names like 401(k) are skipped; the quantity is swapped instead
    assert tamper_number("Brightleaf matches 401(k) contributions up to 4% [1].",
                         "We match 401(k) contributions up to 4% of salary; it vests immediately. Q4 review.") \
        == "Brightleaf matches 401(k) contributions up to 401% [1]."
