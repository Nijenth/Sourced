"""Scoring for the evaluation's "who's asking" cases: greetings, statements and roles the handbook doesn't cover."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("run_eval_whos_asking", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


def reply(found=False, partial=False, clarify=False, text=""):
    return SimpleNamespace(found=found, partial=partial, clarify=clarify, text=text)


def test_greetings_and_statements_must_get_the_invitation():
    case = {"question": "Hi!", "expect_clarify": True}
    assert run_eval.no_answer_ok(case, reply(clarify=True))
    assert not run_eval.no_answer_ok(case, reply())  # a flat "not found" misses the point
    assert not run_eval.no_answer_ok(case, reply(found=True, text="The handbook covers leave, pay and ..."))
    assert run_eval.no_answer_label(case) == "asks what they want"


def test_a_role_the_handbook_does_not_cover():
    case = {"question": "As the CEO, how many days of annual leave do I get?", "role": "CEO"}
    assert run_eval.no_answer_ok(case, reply())
    assert run_eval.no_answer_ok(case, reply(found=True, partial=True, text=(
        "The handbook doesn't mention a CEO. For full-time employees it says 24 days.")))
    # Presenting the full-time rule as the CEO's answer is the mistake these cases catch.
    assert not run_eval.no_answer_ok(case, reply(found=True, text="You get 24 days of annual leave."))
    assert not run_eval.no_answer_ok(case, reply(found=True, partial=True, text="You get 24 days of annual leave."))
    assert run_eval.no_answer_label(case) == "no rule for CEO"


def test_ordinary_unanswerable_questions_still_need_not_found():
    case = {"question": "What time does the Austin studio open on Saturdays?"}
    assert run_eval.no_answer_ok(case, reply())
    assert not run_eval.no_answer_ok(case, reply(found=True, text="9 am"))
    assert not run_eval.no_answer_ok(case, reply(clarify=True))
    assert run_eval.no_answer_label(case) == "not in handbook"


def test_new_cases_have_no_page_and_one_kind_each():
    cases = [c for c in json.loads((ROOT / "eval" / "questions.json").read_text("utf-8")) if c["category"] == "whos_asking"]
    assert len(cases) >= 4
    for case in cases:
        assert case["page"] is None
        assert bool(case.get("role")) != bool(case.get("expect_clarify")), case
