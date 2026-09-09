from __future__ import annotations

import math
import re
from collections import Counter

import numpy as np


TOKEN = re.compile(r"[a-zA-Zа-яА-ЯёЁ0-9+]{2,}")


def tokenize(text: str) -> list[str]:
    return [t.lower() for t in TOKEN.findall(text or "")]


def hashed_embedding(text: str, dim: int = 256) -> list[float]:
    vec = np.zeros(dim, dtype=float)
    tokens = tokenize(text)
    if not tokens:
        return vec.tolist()
    for i, token in enumerate(tokens):
        h = hash(token) % dim
        vec[h] += 1.0
        if i + 1 < len(tokens):
            bigram = hash(token + "_" + tokens[i + 1]) % dim
            vec[bigram] += 0.5
    norm = np.linalg.norm(vec)
    if norm:
        vec /= norm
    return vec.tolist()


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    va, vb = np.array(a), np.array(b)
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)


class BM25:
    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.corpus = corpus
        self.k1 = k1
        self.b = b
        self.n = len(corpus)
        self.avgdl = sum(len(doc) for doc in corpus) / max(self.n, 1)
        df: Counter[str] = Counter()
        for doc in corpus:
            df.update(set(doc))
        self.idf = {
            term: math.log((self.n - freq + 0.5) / (freq + 0.5) + 1) for term, freq in df.items()
        }

    def score(self, query: list[str], index: int) -> float:
        doc = self.corpus[index]
        tf = Counter(doc)
        dl = len(doc) or 1
        total = 0.0
        for term in query:
            if term not in tf:
                continue
            idf = self.idf.get(term, 0.0)
            freq = tf[term]
            total += idf * (freq * (self.k1 + 1)) / (
                freq + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
            )
        return total
