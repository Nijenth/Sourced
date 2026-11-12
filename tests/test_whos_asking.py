"""Greetings and statements get a fixed invitation; answers don't assume a rule for a group the sources don't name."""

import json

from rag.citations import (
    ANSWERED,
    CLARIFY_TEXT,
    NOT_FOUND,
    UNMENTIONED_ASKER,
    build_answer,
    clean_asker,
    mentions,
)
from rag.documents import Chunk
from rag.judge import JUDGE_SYSTEM
from rag.pipeline import ANSWER_SYSTEM, CONDENSE_SYSTEM, REPAIR_PROMPT, RAGPipeline
from rag.store import Hit

SOURCE = "Full-time employees get 24 days of paid annual leave each calendar year."


def hits():
    return [Hit(Chunk("c1", "handbook.pdf", 6, SOURCE), 0.9)]


def reply(coverage, answer="", citations=None, **extra):
    return json.dumps({"coverage": coverage, "answer": answer, "citations": citations or [], **extra})


LEAVE = "Full-time employees get 24 days of paid annual leave each calendar year [1]."
LEAVE_QUOTE = [{"source": 1, "quote": SOURCE}]


def test_clarify_shows_fixed_text_never_the_models():
    answer = build_answer(reply("clarify", "You get 30 days of leave, welcome aboard!"), hits(), "I'm newly hired")
    assert answer.status == NOT_FOUND and not answer.found and answer.clarify
    assert answer.text == CLARIFY_TEXT  # unchecked model text is never shown
    assert answer.citations == []


def test_clarify_is_case_insensitive_and_other_values_are_unchanged():
    assert build_answer(reply(" Clarify "), hits(), "hi").clarify
    assert not build_answer(reply("none", "The documents don't cover this."), hits(), "q").clarify


class ScriptedChat:
    name = label = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, messages, max_tokens=900):
        self.calls.append(system)
        return self.replies.pop(0)


class StaticStore:
    def search(self, query, k, mode="hybrid"):
        return hits()


class FailingJudge:
    def check(self, question, answer):
        raise AssertionError("the judge must not run for a greeting")


def test_pipeline_greeting_gets_the_invitation_without_a_judge():
    chat = ScriptedChat(reply("clarify"))
    answer = RAGPipeline(StaticStore(), chat, judge=FailingJudge(), judge_action="block").ask("hi")
    assert answer.clarify and answer.text == CLARIFY_TEXT
    assert chat.calls == [ANSWER_SYSTEM]  # no history, so no rewrite step


def test_instructions_cover_who_is_asking():
    assert '"clarify"' in ANSWER_SYSTEM and "who is asking" in ANSWER_SYSTEM
    assert "don't assume another group's rule applies to them" in ANSWER_SYSTEM
    assert "As the CEO" in CONDENSE_SYSTEM and "greeting" in CONDENSE_SYSTEM
    assert "clarify" in REPAIR_PROMPT
    assert "different group" in JUDGE_SYSTEM


def test_a_group_the_sources_never_mention_is_flagged_by_the_code():
    answer = build_answer(reply("full", LEAVE, LEAVE_QUOTE, asker="CEO"), hits(), "As the CEO, how much leave do I get?")
    assert answer.status == ANSWERED and answer.found and answer.partial
    assert answer.text == UNMENTIONED_ASKER.format(asker="CEO") + " " + LEAVE
    assert [c.page for c in answer.citations] == [6]


def test_a_group_the_sources_mention_is_left_alone():
    for asker in ("full-time employee", "Full-timer", "part-time employee"):
        answer = build_answer(reply("full", LEAVE, LEAVE_QUOTE, asker=asker), hits(), "q")
        assert answer.text == LEAVE and not answer.partial, asker


def test_no_double_notice_when_the_model_already_names_the_group():
    text = "The handbook doesn't mention a CEO. For full-time employees it gives 24 days [1]."
    answer = build_answer(reply("partial", text, LEAVE_QUOTE, asker="CEO"), hits(), "q")
    assert answer.text == text and answer.partial


def test_no_asker_or_an_unusable_one_changes_nothing():
    for asker in (None, "", 42, ["CEO"], "the", "level 3 engineer", "CEO [1]",
                  "someone who has worked here for many many years now"):
        extra = {} if asker is None else {"asker": asker}
        answer = build_answer(reply("full", LEAVE, LEAVE_QUOTE, **extra), hits(), "q")
        assert answer.text == LEAVE and not answer.partial, asker


def test_asker_is_ignored_when_nothing_was_answered():
    assert build_answer(reply("none", "The documents don't cover this.", asker="CEO"), hits(), "q").text == (
        "The documents don't cover this.")
    assert build_answer(reply("clarify", asker="new hire"), hits(), "q").text == CLARIFY_TEXT


def test_helpers():
    assert clean_asker("  \u201cCEO\u201d. ") == "CEO"
    assert clean_asker("Principal designer") == "Principal designer"
    assert mentions("manager", "Your management chain approves it.")
    assert mentions("Director", "Lead, Principal and Director roles: 8 weeks")
    assert not mentions("CEO", "Full-time employees get 24 days.")
    assert not mentions("dictator", "The People team reviews the handbook.")


def test_instructions_ask_for_the_asker():
    assert '"asker"' in ANSWER_SYSTEM and '"asker"' in REPAIR_PROMPT
