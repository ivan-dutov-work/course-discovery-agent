from __future__ import annotations

from course_discovery.domain.models import UserMemory
from course_discovery.guardrails import redact_pii
from course_discovery.research_agent.cache.scoring import cosine
from course_discovery.research_agent.embeddings import embed_texts


def profile_text(memory: UserMemory) -> str:
    parts = [
        memory.preferred_level or "",
        *memory.preferred_providers,
        *memory.career_goals,
        memory.learning_style_notes or "",
        *(note.text for note in memory.notes),
    ]
    return " ".join(part for part in parts if part)


def profile_vector(memory: UserMemory) -> list[float] | None:
    text = redact_pii(profile_text(memory))
    if not text:
        return None
    vector = embed_texts([text])[0]
    return vector if any(vector) else None


def profile_similarity(memory: UserMemory, course_vector: list[float]) -> float | None:
    vector = profile_vector(memory)
    return None if vector is None else cosine(vector, course_vector)
