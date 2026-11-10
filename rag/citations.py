"""Parse the model's JSON reply and check it against the sources before anything is shown.

The checks fail closed: if the reply is malformed, cites a source that doesn't exist, contains a
quote that isn't in its source, or states a number its cited source doesn't contain, the answer is
blocked and the user sees the closest passages instead.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .documents import collapse, split_sentences
from .store import Hit

# Answer statuses
ANSWERED = "answered"
NOT_FOUND = "not_found"
UNVERIFIED = "unverified"  # well-formed reply that failed the evidence checks
INVALID_OUTPUT = "invalid_output"  # reply wasn't the JSON shape we asked for

BLOCKED_TEXT = (
    "I couldn't verify an answer against the documents, so I'm not showing one. "
    "Here are the closest passages I found."
)

# [1], [1][2] and [1, 2] style markers
_MARKER = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")
_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_TRANSLATE = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',  # curly quotes
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-", "\u2212": "-",  # hyphens, dashes, minus
    "\u00a0": " ", "\u202f": " ", "\u2009": " ",  # no-break and thin spaces
})
# Split answer sentences, but never before a marker, so "24 days. [1]" stays one sentence.
_ANSWER_SPLIT = re.compile(r"(?<=[.!?])\s+(?!\[\d)")
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_NUMBER_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)
_COMPOUND = re.compile(
    r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)[- ](one|two|three|four|five|six|seven|eight|nine)\b",
    re.IGNORECASE,
)


@dataclass
class Citation:
    number: int  # the [n] marker used in the answer
    source: str
    page: int
    quote: str  # text taken from the source itself (never the model's paraphrase)
    verified: bool  # True when the model supplied a quote and it was found in the source


@dataclass
class Answer:
    text: str
    found: bool
    citations: list[Citation] = field(default_factory=list)
    hits: list[Hit] = field(default_factory=list)
    search_query: str = ""
    status: str = ANSWERED
    reason: str = ""  # short explanation when the answer was blocked
    attempts: int = 1  # model calls used to produce the answer
    raw: str = ""  # the model's reply as received, for debugging and evaluation (not shown to users)


class OutputError(ValueError):
    """The model's reply doesn't have the required JSON shape."""


def _norm(text: str) -> str:
    return collapse(text.translate(_TRANSLATE)).lower()


def numbers_in(text: str, ignore_one: bool = False) -> set[str]:
    """Numbers in `text`, normalised so '1,000', '24', '09' and 'twenty-four' compare sensibly.

    `ignore_one` skips the word "one", which is usually not a quantity ("one of the options").
    """
    text = _COMPOUND.sub(lambda m: str(_WORDS[m.group(1).lower()] + _WORDS[m.group(2).lower()]), text)
    text = _NUMBER_WORD.sub(
        lambda m: m.group(0) if ignore_one and m.group(1).lower() == "one" else str(_WORDS[m.group(1).lower()]), text
    )
    found = set()
    for raw in _NUMBER.findall(text):
        n = raw.replace(",", "")
        if "." in n:
            whole, _, frac = n.partition(".")
            frac = frac.rstrip("0")
            n = (whole.lstrip("0") or "0") + ("." + frac if frac else "")
        else:
            n = n.lstrip("0") or "0"
        found.add(n)
    return found


def _marker_numbers(match: re.Match) -> list[int]:
    return [int(x) for x in match.group(1).split(",")]


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


def validate_reply(data: dict) -> tuple[bool, str, list[tuple[int, str]]]:
    """Check the reply's shape strictly. Returns (found, answer, [(source, quote), ...])."""
    found = data.get("found")
    if not isinstance(found, bool):
        raise OutputError('"found" must be true or false')
    answer = data.get("answer")
    if not isinstance(answer, str):
        raise OutputError('"answer" must be text')
    items = data.get("citations")
    if items is None:
        items = []
    if not isinstance(items, list):
        raise OutputError('"citations" must be a list')
    pairs = []
    for item in items:
        if not isinstance(item, dict):
            raise OutputError("each citation must be an object")
        source, quote = item.get("source"), item.get("quote", "")
        if isinstance(source, str) and source.strip().isdigit():
            source = int(source)
        if isinstance(source, bool) or not isinstance(source, int):
            raise OutputError('each citation needs a numeric "source"')
        if not isinstance(quote, str):
            raise OutputError('"quote" must be text')
        pairs.append((source, quote))
    return found, answer.strip(), pairs


_ELLIPSIS = re.compile(r"\s*(?:\.{3,}|\u2026)\s*")


def locate_quote(quote: str, source_text: str, min_ratio: float = 0.85) -> str | None:
    """Like _locate_one, but also accepts quotes stitched from parts with "..." (each part must be found)."""
    parts = [p for p in _ELLIPSIS.split(quote) if len(_norm(p)) >= 8]
    if len(parts) <= 1:
        return _locate_one(quote, source_text, min_ratio)
    found: list[str] = []
    for part in parts:
        located = _locate_one(part, source_text, min_ratio)
        if located is None:
            return None  # any invented part fails the whole quote
        if located not in found:
            found.append(located)
    return " \u2026 ".join(found)


def _locate_one(quote: str, source_text: str, min_ratio: float = 0.85) -> str | None:
    """Return the passage of `source_text` that matches `quote`, or None if it isn't there.

    Small wording differences (punctuation, a dropped word) are tolerated, but every number in the
    quote must appear in the matched passage, so "42 days" never matches "24 days".
    """
    q = _norm(quote)
    if len(q) < 8:
        return None
    q_numbers = numbers_in(quote)
    sentences = split_sentences(collapse(source_text))

    if q in _norm(source_text):
        # Return the source's own wording: the smallest run of sentences containing the quote.
        for size in range(1, len(sentences) + 1):
            for i in range(len(sentences) - size + 1):
                window = " ".join(sentences[i : i + size])
                if q in _norm(window):
                    return window if q_numbers <= numbers_in(window) else None
        window = collapse(source_text)
        return window if q_numbers <= numbers_in(window) else None

    best, best_ratio = None, 0.0
    for size in (1, 2, 3):
        for i in range(len(sentences) - size + 1):
            window = " ".join(sentences[i : i + size])
            if not q_numbers <= numbers_in(window):
                continue
            ratio = SequenceMatcher(None, q, _norm(window)).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    return best if best_ratio >= min_ratio else None


def snippet(text: str, limit: int = 280) -> str:
    text = collapse(text)
    return text if len(text) <= limit else text[: text.rfind(" ", 0, limit)] + " …"


def _answer_sentences(text: str) -> list[str]:
    out = []
    for line in text.splitlines():
        out.extend(s for s in _ANSWER_SPLIT.split(line.strip()) if s.strip())
    return out


def _blocked(status: str, reason: str, hits: list[Hit], query: str) -> Answer:
    return Answer(text=BLOCKED_TEXT, found=False, hits=hits, search_query=query, status=status, reason=reason)


def build_answer(raw: str, hits: list[Hit], search_query: str) -> Answer:
    """Turn the model's raw reply into an Answer, or block it if it can't be verified."""
    answer = _build_answer(raw, hits, search_query)
    answer.raw = raw
    return answer


def _build_answer(raw: str, hits: list[Hit], search_query: str) -> Answer:
    data = parse_model_json(raw)
    if data is None:
        return _blocked(INVALID_OUTPUT, "The model's reply wasn't in the required JSON format.", hits, search_query)
    try:
        found, text, pairs = validate_reply(data)
    except OutputError as exc:
        return _blocked(INVALID_OUTPUT, f"The model's reply was malformed: {exc}.", hits, search_query)

    if not found:
        return Answer(text=text, found=False, hits=hits, search_query=search_query, status=NOT_FOUND)
    if not text:
        return _blocked(INVALID_OUTPUT, "The model said it found an answer but returned no text.", hits, search_query)

    valid = range(1, len(hits) + 1)

    # 1. Every source number must exist.
    marked = [n for m in _MARKER.finditer(text) for n in _marker_numbers(m)]
    listed = [n for n, _ in pairs]
    bad = sorted({n for n in marked + listed if n not in valid})
    if bad:
        return _blocked(UNVERIFIED, f"The answer cites source(s) {bad} that weren't provided.", hits, search_query)

    # 2. Every quote the model gives must really be in its source.
    quotes: dict[int, str] = {}
    for n, quote in pairs:
        if quote.strip() and n not in quotes:
            located = locate_quote(quote, hits[n - 1].chunk.text)
            if located is None:
                return _blocked(UNVERIFIED, f"The quote given for source [{n}] isn't in that source.", hits, search_query)
            quotes[n] = located

    # 3. Every number in the answer must appear in a cited source: the sentence's own sources if it has
    #    markers, otherwise any source the answer cites (models often put one marker after a list).
    all_cited = sorted(set(marked + listed))
    evidence_all = set().union(*(numbers_in(hits[n - 1].chunk.text) for n in all_cited)) if all_cited else set()
    for sentence in _answer_sentences(text):
        cited = [n for m in _MARKER.finditer(sentence) for n in _marker_numbers(m)]
        claim_numbers = numbers_in(_MARKER.sub(" ", sentence), ignore_one=True)
        if not claim_numbers:
            continue  # framing or "not covered" sentences need no marker
        if cited:
            evidence = set().union(*(numbers_in(hits[n - 1].chunk.text) for n in cited))
            where = "the cited source"
        else:
            if not all_cited:
                return _blocked(UNVERIFIED, "A sentence with a specific number has no citation.", hits, search_query)
            evidence, where = evidence_all, "any cited source"
        missing = sorted(claim_numbers - evidence)
        if missing:
            return _blocked(
                UNVERIFIED, f"The number(s) {', '.join(missing)} don't appear in {where}.", hits, search_query
            )

    # 4. Build citations in the order they appear.
    used: list[int] = []
    for n in marked + listed:
        if n not in used:
            used.append(n)
    if not used:
        return _blocked(UNVERIFIED, "The answer has no citations.", hits, search_query)

    citations = []
    for n in used:
        hit = hits[n - 1]
        located = quotes.get(n)
        citations.append(Citation(n, hit.chunk.source, hit.chunk.page, located or snippet(hit.chunk.text), located is not None))
    return Answer(text=text, found=True, citations=citations, hits=hits, search_query=search_query, status=ANSWERED)
