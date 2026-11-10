"""Keyword search (BM25) and rank fusion (RRF), written out in full so the maths is visible.

Why both kinds of search?
- Dense (embedding) search matches meaning: "vacation" finds "annual leave".
- Keyword search matches exact words: "Director" finds the notice-period table row that says "Director",
  even when a page about resigning *sounds* more similar to the question.
Hybrid search runs both and merges the two rankings with Reciprocal Rank Fusion.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter

_TOKEN = re.compile(r"[a-z0-9]+")
# Very common words carry no signal for search; dropping them keeps scores focused on content words.
STOPWORDS = frozenset(
    "a an and are as at be by can do does for from has have how i if in is it its me my of on or our should so "
    "that the their them there they this to was we what when where which who will with would you your much many "
    "get gets got am im".split()
)


def stem(token: str) -> str:
    """A deliberately light stemmer: resigning/resigned -> resign, weeks -> week. Keeps short words intact."""
    if len(token) > 5 and token.endswith("ing"):
        return token[:-3]
    if len(token) > 4 and token.endswith("ed"):
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).lower()
    return [stem(t) for t in _TOKEN.findall(text) if t not in STOPWORDS]


class BM25:
    """Okapi BM25 over a fixed list of documents.

    score(doc, query) = sum over query terms t of
        idf(t) * tf(t, doc) * (k1 + 1) / (tf(t, doc) + k1 * (1 - b + b * len(doc) / avg_len))
    idf(t) = ln(1 + (N - df(t) + 0.5) / (df(t) + 0.5))    # rare words count for more
    """

    def __init__(self, documents: list[str], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs = [Counter(tokenize(d)) for d in documents]
        self.lengths = [sum(c.values()) for c in self.docs]
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.docs else 0.0
        df = Counter(term for doc in self.docs for term in doc)
        n = len(self.docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: str) -> list[float]:
        terms = tokenize(query)
        out = []
        for doc, length in zip(self.docs, self.lengths):
            norm = self.k1 * (1 - self.b + self.b * length / (self.avg_len or 1))
            s = 0.0
            for t in terms:
                tf = doc.get(t, 0)
                if tf:
                    s += self.idf[t] * tf * (self.k1 + 1) / (tf + norm)
            out.append(s)
        return out

    def top(self, query: str, k: int) -> list[tuple[int, float]]:
        """Indexes and scores of the k best documents, best first. Documents with no matching word are left out."""
        ranked = sorted(((i, s) for i, s in enumerate(self.scores(query)) if s > 0), key=lambda x: -x[1])
        return ranked[:k]


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    """Merge several best-first rankings of ids: score(id) = sum of 1 / (k + rank).

    RRF only uses positions, so it can combine scores that aren't comparable (cosine vs BM25).
    Items ranked well by both lists rise to the top. k=60 is the value from the original RRF paper.
    """
    fused: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            fused[item] = fused.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(fused.items(), key=lambda x: -x[1])
