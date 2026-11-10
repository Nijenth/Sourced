"""Parse the model's JSON reply and check every quote against the source text it claims to come from."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .documents import collapse, split_sentences
from .store import Hit

_MARKER = re.compile(r"\[(\d{1,2})\]")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRANSLATE = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


@dataclass
class Citation:
    number: int  # the [n] marker used in the answer
    source: str
    page: int
    quote: str  # text taken from the source itself (never the model's paraphrase)
    verified: bool  # True when the model's quote was found in the source


@dataclass
class Answer:
    text: str
    found: bool
    citations: list[Citation] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    search_query: str = ""


def _norm(text: str) -> str:
    return collapse(text.translate(_TRANSLATE)).lower()


def parse_model_json(raw: str) -> dict | None:
    text = _FENCE.sub("", raw.strip())
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def locate_quote(quote: str, source_text: str, min_ratio: float = 0.85) -> str | None:
    """Return the passage of `source_text` that matches `quote`, or None if it isn't there.

    Exact (normalised) matches return the source's own wording. Otherwise each sentence window
    is compared with difflib, which tolerates small differences such as punctuation or a dropped word.
    """
    q = _norm(quote)
    if len(q) < 8:
        return None
    src_norm = _norm(source_text)
    if q in src_norm:
        # Map back to the original text by matching on sentences that overlap the quote.
        sentences = split_sentences(collapse(source_text))
        picked = [s for s in sentences if _norm(s) in q or q in _norm(s)]
        return " ".join(picked) if picked else quote.strip()

    sentences = split_sentences(collapse(source_text))
    best, best_ratio = None, 0.0
    for size in (1, 2, 3):
        for i in range(len(sentences) - size + 1):
            window = " ".join(sentences[i : i + size])
            ratio = SequenceMatcher(None, q, _norm(window)).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    return best if best_ratio >= min_ratio else None


def _snippet(text: str, limit: int = 280) -> str:
    text = collapse(text)
    return text if len(text) <= limit else text[: text.rfind(" ", 0, limit)] + " …"


def build_answer(raw: str, hits: list[Hit], search_query: str) -> Answer:
    """Turn the model's raw reply into an Answer with verified, page-level citations."""
    data = parse_model_json(raw)
    if data is None:
        # The model ignored the JSON format. Keep its text and cite whatever markers it used.
        data = {"found": True, "answer": raw.strip(), "citations": []}

    found = bool(data.get("found", True))
    text = str(data.get("answer", "")).strip()
    if not found:
        return Answer(text=text, found=False, hits=hits, search_query=search_query)

    quotes: dict[int, str] = {}
    for item in data.get("citations") or []:
        try:
            n = int(item.get("source"))
        except (TypeError, ValueError, AttributeError):
            continue
        if 1 <= n <= len(hits) and n not in quotes:
            quotes[n] = str(item.get("quote", ""))

    used = []
    for m in _MARKER.finditer(text):
        n = int(m.group(1))
        if 1 <= n <= len(hits) and n not in used:
            used.append(n)
    for n in quotes:  # cited in the list but not marked in the text
        if n not in used:
            used.append(n)

    # Drop markers that point at sources that don't exist.
    text = _MARKER.sub(lambda m: m.group(0) if 1 <= int(m.group(1)) <= len(hits) else "", text)

    citations = []
    for n in used:
        hit = hits[n - 1]
        located = locate_quote(quotes.get(n, ""), hit.chunk.text) if quotes.get(n) else None
        citations.append(
            Citation(n, hit.chunk.source, hit.chunk.page, located or _snippet(hit.chunk.text), located is not None)
        )

    if not text:
        return Answer(text="", found=False, hits=hits, search_query=search_query)
    return Answer(text=text, found=True, citations=citations, hits=hits, search_query=search_query)
