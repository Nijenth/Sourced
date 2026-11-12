"""Greetings and statements get a fixed invitation; answers don't assume a rule for a group the sources don't name."""

import json

from rag.citations import CLARIFY_TEXT, NOT_FOUND, build_answer
from rag.documents import Chunk
from rag.judge import JUDGE_SYSTEM
from rag.pipeline import ANSWER_SYSTEM, CONDENSE_SYSTEM, REPAIR_PROMPT, RAGPipeline
from rag.store import Hit

SOURCE = "Full-time employees get 24 days of paid annual leave each calendar year."


def hits():
    return [Hit(Chunk("c1", "handbook.pdf", 6, SOURCE), 0.9)]


def reply(coverage, answer="", citations=None):
    return json.dumps({"coverage": coverage, "answer": answer, "citations": citations or []})


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
