"""The evaluation's own scoring rules. A wrong scorer blames the model for nothing (see the 31\u202fMarch bug)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("run_eval", ROOT / "eval" / "run_eval.py")
run_eval = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_eval)


def test_space_variants_hyphens_and_number_words_match():
    assert run_eval.has_all("used by 31\u202fMarch", ["31 March"])
    assert run_eval.has_all("return it within five working days", ["5 working days"])
    assert run_eval.has_all("a twenty-four day allowance", ["24"])
    assert run_eval.has_all("leave is pro-rated", [["prorated", "pro rata"]])
    assert run_eval.has_all("leave is pro\u2011rated", [["prorated", "pro rata"]])  # non-breaking hyphen (Groq)
    assert run_eval.has_all("$1,500 per year", ["1,500"])


def test_every_expected_fact_must_be_present():
    assert not run_eval.has_all("8 weeks of notice", ["8 weeks", "5 working days"])


def test_page_metrics_any_vs_all():
    assert run_eval.page_metrics([10], [6, 10, 3]) == (True, 0.5)
    assert run_eval.page_metrics([10], [6, 3]) == (False, 0.0)
    assert run_eval.page_metrics([4, 15], [4, 2], needs_all=True) == (False, 1.0)
    assert run_eval.page_metrics([4, 15], [15, 4], needs_all=True) == (True, 1.0)


def test_question_file_is_well_formed():
    cases = json.loads((ROOT / "eval" / "questions.json").read_text(encoding="utf-8"))
    assert len(cases) >= 35
    for c in cases:
        assert c["question"] and c.get("category")
        pages = run_eval.gold_pages(c)
        assert all(1 <= p <= 16 for p in pages)
        if pages:
            assert c["expect"], f"answerable question needs expected facts: {c['question']}"
