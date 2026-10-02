"""Okapi BM25 over chunk text: the lexical ranking used when there are no
embedding vectors (no embeddings provider configured).

The identifier index (keyword_index.py) only scores exact identifiers, so a
question in plain words ("when is the interrupt line cleared") ties every
chunk that shares one identifier. BM25 ranks by all words, weighting rare ones
up and long chunks down. Built in memory from the stored chunks at load time
(a few hundred chunks: milliseconds), so nothing extra is persisted.
"""

from __future__ import annotations

import math
import re
from collections import Counter

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*|\d+")
_STOP = {"the", "a", "an", "is", "are", "of", "to", "in", "on", "and", "or", "for", "it",
         "be", "as", "by", "at", "this", "that", "with", "when", "what", "which", "how",
         "does", "do", "if", "from", "its", "into", "not", "no"}


def tokens(text: str) -> list[str]:
    """Words and identifier parts (r_gpio_inten -> r, gpio, inten), lower case."""
    out = []
    for t in _TOKEN.findall(text.replace("_", " ")):
        t = t.lower()
        if t not in _STOP and len(t) > 1:
            out.append(t)
    return out


class BM25:
    def __init__(self, docs: dict[str, str], k1: float = 1.4, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = {cid: Counter(tokens(text)) for cid, text in docs.items()}
        self.len = {cid: sum(c.values()) for cid, c in self.tf.items()}
        self.avg = (sum(self.len.values()) / len(self.len)) if self.len else 1.0
        df = Counter(t for c in self.tf.values() for t in c)
        n = len(self.tf)
        self.idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def search(self, query: str, limit: int = 20) -> list[tuple[str, float]]:
        q = set(tokens(query))
        scores = []
        for cid, c in self.tf.items():
            s = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.len[cid] / self.avg)
            for t in q:
                f = c.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + norm)
            if s > 0:
                scores.append((cid, s))
        scores.sort(key=lambda x: -x[1])
        return scores[:limit]
