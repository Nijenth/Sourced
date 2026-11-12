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

These are all the passages the answer was written from; its [1], [2] markers refer to them.
For each factual claim in the answer, decide whether the passages state it. Numbers, names, roles,
conditions and exceptions must match the passages exactly. If the answer says the documents don't
give something but one of these passages does give it, that statement is unsupported.
If the question says who is asking (a role or situation) and the answer applies a rule the passages
give for a different group as if it were about them, without saying so, that claim is unsupported.

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
    judge: str = ""  # short name of the provider that judged, e.g. "GroqCloud"

    @property
    def score(self) -> float | None:
        """1.0 supported, 0.5 partial, 0.0 unsupported, None if the check couldn't run."""
        return {"supported": 1.0, "partial": 0.5, "unsupported": 0.0}.get(self.verdict)


class Judge:
    def __init__(self, chat: ChatModel):
        from .providers import PROVIDERS

        self.chat = chat
        self.name = getattr(chat, "label", getattr(chat, "name", "judge"))
        provider = PROVIDERS.get(getattr(chat, "name", ""))
        self.short_name = provider.label if provider else getattr(chat, "name", "judge")

    def check(self, question: str, answer: Answer) -> Judgement:
        if not answer.citations:
            return Judgement("error", ["The answer cites no passages to check against."], judge=self.short_name)
        # All retrieved passages, numbered as the answer's markers, so a false "the documents don't say"
        # can be caught when another passage does say it.
        passages = [f"[{i}] {h.chunk.source}, page {h.chunk.page}:\n{h.chunk.text}" for i, h in enumerate(answer.hits, 1)]
        user = (f"Question: {question}\n\nPassages:\n\n" + "\n\n".join(passages)
                + f"\n\nAnswer to check:\n{answer.text}")
        try:
            raw = self.chat.complete(JUDGE_SYSTEM, [{"role": "user", "content": user}], max_tokens=400)
        except ProviderError as exc:
            return Judgement("error", [str(exc)], judge=self.short_name)
        data = parse_model_json(raw)
        verdict = data.get("verdict") if data else None
        if not isinstance(verdict, str) or verdict.strip().lower() not in VERDICTS:
            return Judgement("error", ["The accuracy check gave an unreadable reply."], raw, self.short_name)
        claims = data.get("unsupported_claims") or []
        problems = [str(c).strip() for c in claims if str(c).strip()] if isinstance(claims, list) else []
        return Judgement(verdict.strip().lower(), problems[:10], raw, self.short_name)


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
