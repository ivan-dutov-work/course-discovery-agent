from __future__ import annotations

from course_discovery.research_agent.embeddings import embed_texts

MIN_TOPIC_SIMILARITY = 0.12
SIMILARITY_WEIGHT = 0.7
CONFIDENCE_WEIGHT = 0.2
USE_COUNT_WEIGHT = 0.1
USE_COUNT_CAP = 10


def topic_vector(topic: str) -> list[float] | None:
    vector = embed_texts([topic])[0]
    return vector if any(vector) else None


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def topic_score(similarity: float, confidence: float, use_count: int) -> float:
    return (
        SIMILARITY_WEIGHT * similarity
        + CONFIDENCE_WEIGHT * confidence
        + USE_COUNT_WEIGHT * min(use_count, USE_COUNT_CAP) / USE_COUNT_CAP
    )
