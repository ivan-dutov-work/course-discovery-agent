from __future__ import annotations

import hashlib
import math
import re
from collections import Counter

from course_discovery.research_agent.embeddings.base import EMBEDDING_DIMENSION

_TOKEN = re.compile(r"[a-z0-9+#]+")
_STOPWORDS = frozenset(
    "a an and are as at be by course courses find for from in into is it of on or the to with "
    "your free beginner intermediate advanced certificate certification certified".split()
)


def _stem(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    stems = (_stem(t) for t in _TOKEN.findall(text.lower()))
    return [t for t in stems if t not in _STOPWORDS]


def _bucket(feature: str, dimension: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
    value = int.from_bytes(digest, "big")
    return value % dimension, 1.0 if (value >> 63) & 1 else -1.0


class HashingEmbedder:
    """Lexical feature-hashed embedding: shared words and word pairs, not meaning."""

    def __init__(self, dimension: int = EMBEDDING_DIMENSION) -> None:
        self.dimension = dimension

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        tokens = _tokens(text)
        features = Counter(tokens)
        features.update(f"{a} {b}" for a, b in zip(tokens, tokens[1:]))
        vector = [0.0] * self.dimension
        for feature, count in features.items():
            index, sign = _bucket(feature, self.dimension)
            vector[index] += sign * (1.0 + math.log(count))
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0.0:
            return vector
        return [v / norm for v in vector]
