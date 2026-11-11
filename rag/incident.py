"""The sample handbook's lost-device report: a fixed template, so nothing typed here is sent to an AI model."""
from __future__ import annotations

# From the fictional Brightleaf handbook, page 12 ("Lost or stolen devices"). One place to update.
POLICY = {"email": "it@brightleaf.example", "hours": 24, "page": 12}


def policy_note() -> str:
    return (f"Brightleaf’s sample handbook (page {POLICY['page']}) says to report a lost or stolen laptop or phone "
            f"to {POLICY['email']} within {POLICY['hours']} hours.")


def format_incident_draft(device: str, when: str, description: str) -> str | None:
    """The draft email, or None if there is no description. Empty optional fields say "(not provided)"."""
    description = description.strip()
    if not description:
        return None

    def field(value: str) -> str:
        return value.strip() or "(not provided)"

    return (f"To: {POLICY['email']}\n"
            "Subject: Lost or stolen device report\n\n"
            f"Affected device: {field(device)}\n"
            f"When noticed: {field(when)}\n\n"
            f"What happened:\n{description}")
