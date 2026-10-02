from __future__ import annotations

from course_discovery.domain.models import (
    CourseCandidate,
    EvidenceItem,
    SearchFilters,
    UserMemory,
)
from course_discovery.research_agent.cache.scoring import (
    MIN_TOPIC_SIMILARITY,
    cosine,
    topic_score,
    topic_vector,
)
from course_discovery.research_agent.embeddings import course_text, embed_texts
from course_discovery.research_agent.memory.profile_vector import profile_vector


def seed_cache(
    filters: SearchFilters,
    memory: UserMemory,
    *,
    limit: int,
) -> list[CourseCandidate]:
    seed = [
        CourseCandidate(
            title="Python for Everybody",
            provider="coursera",
            url="https://www.coursera.org/specializations/python",
            description="Beginner-friendly Python specialization with assignments.",
            price="Free to audit; paid certificate optional",
            is_free=True,
            has_certificate=True,
            level="beginner",
            language="en",
            rating=4.8,
            published_or_updated="2025-11-15",
            duration_hours=60,
            source="cache",
            evidence=[
                EvidenceItem(
                    source_url="https://www.coursera.org/specializations/python",
                    quote_or_summary="Course page evidence indicates free audit access and optional certificate.",
                    supports=["free", "certificate", "beginner", "python"],
                )
            ],
            confidence=0.82,
        ),
        CourseCandidate(
            title="CS50's Introduction to Programming with Python",
            provider="edx",
            url="https://www.edx.org/learn/python/harvard-university-cs50-s-introduction-to-programming-with-python",
            description="Introductory Harvard Python course with rigorous exercises.",
            price="Free to audit; paid certificate optional",
            is_free=True,
            has_certificate=True,
            level="beginner",
            language="en",
            rating=4.9,
            published_or_updated="2025-12-01",
            duration_hours=10,
            source="cache",
            evidence=[
                EvidenceItem(
                    source_url="https://www.edx.org/learn/python/harvard-university-cs50-s-introduction-to-programming-with-python",
                    quote_or_summary="Course page evidence indicates free audit access and certificate option.",
                    supports=["free", "certificate", "beginner", "python"],
                )
            ],
            confidence=0.86,
        ),
    ]
    query = topic_vector(filters.topic)
    profile = profile_vector(memory)
    ranked: list[tuple[float, CourseCandidate]] = []
    for course in seed:
        if (
            course.url in memory.completed_course_urls
            or course.url in memory.rejected_course_urls
            or (course.provider or "") in memory.avoided_providers
        ):
            continue
        similarity = 0.0
        course_vector = embed_texts([course_text(course.title, course.description, [])])[0]
        if query is not None:
            similarity = cosine(query, course_vector)
            if similarity < MIN_TOPIC_SIMILARITY:
                continue
        if profile is not None:
            course = course.model_copy(update={"profile_similarity": cosine(profile, course_vector)})
        ranked.append((topic_score(similarity, course.confidence, 0), course))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [course for _, course in ranked[:limit]]
