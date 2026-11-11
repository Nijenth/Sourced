"""Faithfulness judge: a second model checks each claim in an answer against the passages it cites.

The deterministic checks in citations.py prove that quotes and numbers *exist* in the sources. They
can't tell whether a claim *matches* them, e.g. "Directors give 4 weeks' notice" when the table row
says 8 weeks but 4 weeks appears in the row above. A judge model reading the answer next to the
passages can. It is still a model and can be wrong, so by default its verdict is shown as a warning
rather than used to hide answers (see JUDGE_ACTION).

Use a different model from the one that wrote the answer where possible: models tend to approve
their own wording.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .citations import Answer, numbers_in, parse_model_json
from .providers.base import ChatModel, ProviderError

VERDICTS = ("supported", "partial", "unsupported")

JUDGE_SYSTEM = """You check an answer against source passages. You did not write the answer.

For each factual claim in the answer, decide whether the passages state it. Numbers, names, roles,
conditions and exceptions must match the passages exactly. Saying that the documents don't cover
something is not a claim. Ignore the [1], [2] markers.

Only check what the answer says. Do not mark it down for details it leaves out, or for gaps in the
documents themselves: an incomplete but accurate answer is "supported".

Reply with JSON only, in this shape:
{"verdict": "supported" | "partial" | "unsupported", "unsupported_claims": ["short description", ...]}

- "supported": every claim is stated in the passages.
- "partial": the main point is supported, but some detail is not.
- "unsupported": a main claim contradicts the passages or is not in them.
List every claim that is not supported (empty list if none)."""


@dataclass
class Judgement:
    verdict: str  # supported | partial | unsupported | error
    problems: list[str] = field(default_factory=list)
    raw: str = ""

    @property
    def score(self) -> float | None:
        """1.0 supported, 0.5 partial, 0.0 unsupported, None if the check couldn't run."""
        return {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}.get(self.verdict)


class Judge:
    def __init__(self, chat: ChatModel):
        self.chat = chat
        self.name = getattr(chat, "label", getattr(chat, "name", "judge"))

    def check(self, question: str, answer: Answer) -> Judgement:
        if not answer.citations:
            return Judgement("error", ["The answer cites no passages to check against."])
        seen, passages = set(), []
        for c in answer.citations:
            if c.number in seen or not (1 <= c.number <= len(answer.hits)):
                continue
            seen.add(c.number)
            chunk = answer.hits[c.number - 1].chunk
            passages.append(f"[{c.number}] {chunk.source}, page {chunk.page}:\n{chunk.text}")
        user = (f"Question: {question}\n\nPassages:\n\n" + "\n\n".join(passages)
                + f"\n\nAnswer to check:\n{answer.text}")
        try:
            raw = self.chat.complete(JUDGE_SYSTEM, [{"role": "user", "content": user}], max_tokens=400)
        except ProviderError as exc:
            return Judgement("error", [str(exc)])
        data = parse_model_json(raw)
        verdict = data.get("verdict") if data else None
        if not isinstance(verdict, str) or verdict.strip().lower() not in VERDICTS:
            return Judgement("error", ["The accuracy check gave an unreadable reply."], raw)
        claims = data.get("unsupported_claims") or []
        problems = [str(c).strip() for c in claims if str(c).strip()] if isinstance(claims, list) else []
        return Judgement(verdict.strip().lower(), problems[:10], raw)


def tamper_number(answer_text: str, evidence_text: str) -> str | None:
    """For testing the judge: swap the first number in an answer for a *different* number from the
    same evidence (the hardest kind of error for the deterministic checks). None if the answer has no
    number outside the [n] markers."""
    # Skip [n] markers and numbers that are part of names, such as "401(k)" or "Q4".
    match = re.search(r"(?<![\[\d.,A-Za-z])\d+(?:[.,]\d+)?(?![\]\d(A-Za-z])", answer_text)
    if not match:
        return None
    original = match.group(0).replace(",", "")
    others = sorted(n for n in numbers_in(evidence_text) if n != original.lstrip("0"))
    replacement = others[0] if others else str(int(float(original)) + 1)
    return answer_text[: match.start()] + replacement + answer_text[match.end():]
