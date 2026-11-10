"""Offline stand-ins for the embedding and chat APIs.

Used by the test suite, and by the app when RAG_OFFLINE_MODE=1 so you can work on the
interface without API keys. They match keywords; they are not AI and are never used
in the deployed demo.
"""
from __future__ import annotations

import hashlib
import json
import math
import re

from .documents import split_sentences

_WORD = re.compile(r"[a-z0-9$]+")
_STOP = set(
    "a an the of to in on for and or is are do does i my we our you your be can how what when where who which "
    "with at by from it this that there as if any much many get have has will after before about".split()
)


def _tokens(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in _STOP:
            continue
        out.append(w[:-1] if len(w) > 4 and w.endswith("s") else w)  # crude plural folding
    return out


class HashingEmbedder:
    """Bag-of-words vectors via feature hashing. Deterministic, no network."""

    name = "offline:hashing"

    def __init__(self, dims: int = 512):
        self.dims = dims

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            v = [0.0] * self.dims
            for tok in _tokens(text):
                h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
                v[h % self.dims] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            vectors.append([x / norm for x in v])
        return vectors


class KeywordChat:
    """Answers by quoting the source sentence with the most keyword overlap."""

    name = "offline"
    label = "Offline test mode (keyword matching, not AI)"

    def __init__(self, min_overlap: float = 0.6):
        self.min_overlap = min_overlap

    def complete(self, system: str, messages: list[dict], max_tokens: int = 900) -> str:
        content = messages[-1]["content"]
        if "Sources:" not in content:  # follow-up rewrite request: keep the question as is
            return content.rsplit("Latest question:", 1)[-1].strip()

        sources_part, question = content.rsplit("Question:", 1)
        q = set(_tokens(question))
        best = (0.0, 0, "")
        for block in re.split(r"\n\n(?=\[\d+\] )", sources_part.split("Sources:", 1)[1].strip()):
            m = re.match(r"\[(\d+)\][^\n]*\n(.*)", block, re.S)
            if not m:
                continue
            n, text = int(m.group(1)), m.group(2)
            for sent in split_sentences(text):
                overlap = len(q & set(_tokens(sent))) / max(1, len(q))
                if overlap > best[0]:
                    best = (overlap, n, sent)

        if best[0] < self.min_overlap:
            return json.dumps({"found": False, "answer": "The documents don't cover this.", "citations": []})
        return json.dumps(
            {"found": True, "answer": f"{best[2]} [{best[1]}]", "citations": [{"source": best[1], "quote": best[2]}]}
        )
