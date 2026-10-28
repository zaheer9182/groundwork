"""Lexical ranking and rank fusion. No external index, no embeddings service.

The source's own ranking (Microsoft Search) already knows titles, freshness
and popularity. BM25 over chunks knows which passage answers the question.
Reciprocal rank fusion combines both without having to calibrate scores.
"""
from __future__ import annotations

import math
import re
from collections import Counter

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    ["a", "an", "and", "are", "as", "at", "be", "but", "by", "can", "do", "does", "for", "from", "has", "have", "how", "i", "if", "in", "is", "it", "its", "me", "my", "of", "on", "or", "our", "should", "so", "that", "the", "their", "there", "this", "to", "was", "we", "what", "when", "where", "which", "who", "why", "will", "with", "would", "you", "your"]
)


def tokenize(text: str) -> list[str]:
    out = []
    for w in _WORD.findall(text.lower()):
        if w in STOPWORDS:
            continue
        # light stemming: claims/claim, approvals/approval
        if len(w) > 4 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        out.append(w)
    return out


def bm25(query: str, texts: list[str], k1: float = 1.5, b: float = 0.75) -> list[tuple[int, float]]:
    """Return (index, score) pairs sorted best first."""
    q = tokenize(query)
    if not q or not texts:
        return []
    docs = [tokenize(t) for t in texts]
    n = len(docs)
    avg = (sum(len(d) for d in docs) / n) or 1.0
    df = Counter(t for d in docs for t in set(d))
    results = []
    for i, d in enumerate(docs):
        tf = Counter(d)
        score = 0.0
        for term in q:
            f = tf.get(term)
            if not f:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg))
        results.append((i, score))
    results.sort(key=lambda x: x[1], reverse=True)
    return results


def coverage(query: str, text: str) -> float:
    """Share of distinct query terms that appear in the text. Unlike a BM25
    score it does not depend on how many candidates came back, so it works as
    a "nothing here matches" gate for a one-document tenant and a big one."""
    q = set(tokenize(query))
    if not q:
        return 0.0
    return len(q & set(tokenize(text))) / len(q)


def fuse(rankings: list[list[int]], k: int = 60) -> list[tuple[int, float]]:
    """Reciprocal rank fusion over several best-first lists of item ids."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for pos, item in enumerate(ranking):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + pos + 1)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
