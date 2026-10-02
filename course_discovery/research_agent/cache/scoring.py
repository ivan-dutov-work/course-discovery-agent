from __future__ import annotations

from course_discovery.research_agent.embeddings import embed_texts, get_embedder
from course_discovery.research_agent.embeddings.facets import strip_facets

MIN_TOPIC_SIMILARITY = 0.12
SIMILARITY_WEIGHT = 0.7
CONFIDENCE_WEIGHT = 0.2
USE_COUNT_WEIGHT = 0.1
USE_COUNT_CAP = 10


def topic_floor() -> float:
    return getattr(get_embedder(), "topic_floor", MIN_TOPIC_SIMILARITY)


def topic_relative_cutoff() -> float:
    return getattr(get_embedder(), "relative_cutoff", 0.0)


def topic_vector(topic: str) -> list[float] | None:
    text = strip_facets(topic)
    if not text:
        return None
    vector = embed_texts([text])[0]
    return vector if any(vector) else None


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def topic_score(similarity: float, confidence: float, use_count: int) -> float:
    return (
        SIMILARITY_WEIGHT * similarity
        + CONFIDENCE_WEIGHT * confidence
        + USE_COUNT_WEIGHT * min(use_count, USE_COUNT_CAP) / USE_COUNT_CAP
    )
