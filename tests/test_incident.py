"""The sample-mode incident report draft (a fixed template; no AI involved)."""
from __future__ import annotations

from pathlib import Path

from rag.documents import load_pdf
from rag.incident import POLICY, format_incident_draft, policy_note

PDF = Path(__file__).resolve().parent.parent / "data" / "sample" / "Brightleaf_Studio_Employee_Handbook.pdf"


def test_draft_contains_every_field():
    draft = format_incident_draft("  Laptop ", "This morning", " Left on the train ")
    assert draft.startswith("To: it@brightleaf.example\nSubject: Lost or stolen device report")
    assert "Affected device: Laptop" in draft and "When noticed: This morning" in draft
    assert draft.endswith("What happened:\nLeft on the train")


def test_empty_optional_fields_say_not_provided():
    draft = format_incident_draft("", "   ", "Phone missing")
    assert "Affected device: (not provided)" in draft and "When noticed: (not provided)" in draft


def test_no_description_means_no_draft():
    assert format_incident_draft("Laptop", "today", "   ") is None


def test_policy_facts_match_the_handbook():
    page = load_pdf(PDF.read_bytes(), PDF.name)[POLICY["page"] - 1].text
    assert POLICY["email"] in page and f"{POLICY['hours']} hours" in page
    assert POLICY["email"] in policy_note()
