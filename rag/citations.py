"""Parse the model's JSON reply and check it against the sources before anything is shown.

The checks fail closed: if the reply is malformed, cites a source that doesn't exist, contains a
quote that isn't in its source, or states a number its cited source doesn't contain, the answer is
blocked and the user sees the closest passages instead.
"""
from __future__ import annotations

import json
from collections import Counter
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from .documents import collapse, split_sentences
from .lexical import tokenize
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
    partial: bool = False  # the documents answer only part of the question (or give a rule, not a figure)
    clarify: bool = False  # the message was a greeting or statement, not a question: text is CLARIFY_TEXT
    judgement: object = None  # rag.judge.Judgement when an accuracy check ran


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


COVERAGE = ("full", "partial", "none", "clarify")
# Shown when the message is a greeting or a statement rather than a question. Fixed text: nothing a model
# writes is shown without the checks below.
CLARIFY_TEXT = "Happy to help. What would you like to know? Ask me about anything in the documents."


def validate_reply(data: dict) -> tuple[bool, bool, str, list[tuple[int, str]]]:
    """Check the reply's shape strictly. Returns (found, partial, answer, [(source, quote), ...]).

    Replies say "coverage": "full" | "partial" | "none". The older "found": true/false is still accepted.
    """
    if "coverage" in data:
        coverage = data.get("coverage")
        if not isinstance(coverage, str) or coverage.strip().lower() not in COVERAGE:
            raise OutputError('"coverage" must be "full", "partial", "none" or "clarify"')
        coverage = coverage.strip().lower()
        found, partial = coverage not in ("none", "clarify"), coverage == "partial"
    else:
        found, partial = data.get("found"), False
        if not isinstance(found, bool):
            raise OutputError('"coverage" must be "full", "partial", "none" or "clarify"')
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
    return found, partial, answer.strip(), pairs


# Models stitch quotes from pieces with "..." or table cells with "|". Each piece must be in the source.
_ELLIPSIS = re.compile(r"\s*(?:\.{3,}|\u2026|\|)\s*")


def locate_quote(quote: str, source_text: str, min_ratio: float = 0.85) -> str | None:
    """Like _locate_one, but also accepts quotes stitched from pieces (each piece must be found):
    parts joined with "..." or "|", and several sentences that skip sentences in between.

    Known limitation: this proves every piece is in the source, not that the pieces belong together.
    The number check in build_answer still applies to the answer itself.
    """
    whole = _locate_one(quote, source_text, min_ratio)
    if whole is not None:
        return whole
    parts = [s for p in _ELLIPSIS.split(quote) for s in split_sentences(collapse(p)) if len(_norm(s)) >= 8]
    if not parts:
        return None
    found: list[str] = []
    for part in parts:
        located = _locate_one(part, source_text, min_ratio)
        if located is None:
            found = []
            break
        if located not in found:
            found.append(located)
    if found:
        return " \u2026 ".join(found)
    # Last resort for quotes rebuilt in the model's own layout (typically table rows): check the content,
    # not the order. Nearly all meaningful words and every number must be in the source.
    return _covered(quote, source_text)


MIN_COVERAGE = 0.9
# A missing negation flips the meaning ("can be extended" vs "can never be extended"), so it is never allowed.
NEGATIONS = frozenset({"not", "no", "never", "none", "cannot", "nor", "neither", "without", "nothing", "nobody"})


def _content_words(text: str) -> list[str]:
    return tokenize(re.sub(r"n['\u2019]t\b", " not", text, flags=re.IGNORECASE))


def _covered(quote: str, source_text: str) -> str | None:
    """Layout-insensitive check, only for table-like fragments (no full sentences, no negations).

    Full sentences must match closely (above). Known limitation: inside a table row, a value swapped for
    another value from the same table can pass this check; an LLM faithfulness check would be needed for that.
    """
    words = _content_words(quote)
    if re.search(r"[.!?](?:\s|$)", _ELLIPSIS.sub(" ", quote)) or any(w in NEGATIONS for w in words):
        return None  # "..." and "|" mark gaps, not sentence ends
    if len(words) < 4 or not numbers_in(quote) <= numbers_in(source_text):
        return None
    source_words = set(_content_words(source_text))
    missing = [w for w in words if w not in source_words]
    if any(w in NEGATIONS for w in missing) or 1 - len(missing) / len(words) < MIN_COVERAGE:
        return None
    # Show the source's own words: the 1-3 sentence window that contains the most of the quote's words.
    sentences = split_sentences(collapse(source_text))
    best, best_hits = None, -1
    for size in (1, 2, 3):
        for i in range(len(sentences) - size + 1):
            window = " ".join(sentences[i : i + size])
            hits = len(set(words) & set(tokenize(window)))
            if hits > best_hits:
                best, best_hits = window, hits
    return snippet(best or source_text, 400)


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

    q_contexts, q_negations = _number_contexts(quote), _negations(quote)
    best, best_ratio = None, 0.0
    for size in (1, 2, 3):
        for i in range(len(sentences) - size + 1):
            window = " ".join(sentences[i : i + size])
            if not q_numbers <= numbers_in(window):
                continue
            # "up to 90 days" must not match "up to 60 days" just because "90" appears elsewhere nearby.
            if not q_contexts <= _number_contexts(window):
                continue
            # An added or dropped "not" flips the meaning even when almost every character matches.
            w_negations = _negations(window)
            if (q_negations != w_negations) if size == 1 else (q_negations - w_negations):
                continue
            ratio = SequenceMatcher(None, q, _norm(window)).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    return best if best_ratio >= min_ratio else None


_CONTEXT_TOKEN = re.compile(r"[a-z]+|\d+(?:[.,]\d+)*")


def _number_contexts(text: str) -> set[tuple[str, str]]:
    """(previous word, number) pairs, e.g. "up to 60 days" -> {("to", "60")}."""
    tokens = _CONTEXT_TOKEN.findall(_norm(text))
    pairs = set()
    for i, t in enumerate(tokens):
        if t[0].isdigit():
            pairs.add((tokens[i - 1] if i else "", t.replace(",", "").lstrip("0") or "0"))
    return pairs


def _negations(text: str) -> Counter:
    return Counter(w for w in _content_words(text) if w in NEGATIONS)


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
        found, partial, text, pairs = validate_reply(data)
    except OutputError as exc:
        return _blocked(INVALID_OUTPUT, f"The model's reply was malformed: {exc}.", hits, search_query)

    if str(data.get("coverage", "")).strip().lower() == "clarify":
        return Answer(text=CLARIFY_TEXT, found=False, hits=hits, search_query=search_query, status=NOT_FOUND, clarify=True)
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

    # 3. Every number in the answer must appear in at least one source the answer cites. Invented numbers
    #    are blocked here. Whether a real number is attached to the right claim (e.g. under the right
    #    marker, or the right table row) is a question of meaning: that is the accuracy judge's job.
    all_cited = sorted(set(marked + listed))
    evidence = set().union(*(numbers_in(hits[n - 1].chunk.text) for n in all_cited)) if all_cited else set()
    for sentence in _answer_sentences(text):
        claim_numbers = numbers_in(_MARKER.sub(" ", sentence), ignore_one=True)
        if not claim_numbers:
            continue  # framing or "not covered" sentences need no marker
        if not all_cited:
            return _blocked(UNVERIFIED, "A sentence with a specific number has no citation.", hits, search_query)
        missing = sorted(claim_numbers - evidence)
        if missing:
            return _blocked(
                UNVERIFIED, f"The number(s) {', '.join(missing)} don't appear in any cited source.", hits, search_query
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

    # 5. Who's asking. The model names the group the person says they belong to ("CEO"); the code, not the model,
    #    decides whether the sources mention it. If they don't, the answer can't be about that group, so it says so.
    asker = clean_asker(data.get("asker"))
    if asker and not mentions(asker, text) and not any(mentions(asker, hit.chunk.text) for hit in hits):
        text = UNMENTIONED_ASKER.format(asker=asker) + " " + text
        partial = True
    return Answer(text=text, found=True, citations=citations, hits=hits, search_query=search_query, status=ANSWERED,
                  partial=partial)


UNMENTIONED_ASKER = "The documents don't mention “{asker}”, so check whether this applies to you."
_ASKER_WORD = re.compile(r"[^\W\d_]+")
_ASKER_STOP = frozenset({"a", "an", "the", "and", "or", "of", "for", "in", "at", "on", "to", "my", "our", "i", "am", "im"})


def clean_asker(value) -> str:
    """The model's "asker" field as a short plain label, or "" if it's missing or doesn't look like a label."""
    if not isinstance(value, str):
        return ""
    label = collapse(value.translate(_TRANSLATE)).strip(" \"'.,;:!?()[]")
    # Only short, letters-only labels: no numbers (they'd dodge the number check), markers or sentences.
    if not label or len(label) > 40 or len(label.split()) > 4 or not re.fullmatch(r"[^\W\d_]+(?:[ '-][^\W\d_]+)*", label):
        return ""
    if not any(w not in _ASKER_STOP for w in _ASKER_WORD.findall(label.lower())):
        return ""
    return label


def mentions(label: str, text: str) -> bool:
    """True if any word of `label` appears in `text`, allowing simple word endings ("manager" ~ "management")."""
    words = {w for w in _ASKER_WORD.findall(_norm(text))}
    for word in _ASKER_WORD.findall(label.lower()):
        if word in _ASKER_STOP:
            continue
        if len(word) <= 4:
            if word in words:
                return True
        elif any(other.startswith(word[:5]) for other in words):
            return True
    return False
