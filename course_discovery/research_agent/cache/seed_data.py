from __future__ import annotations

from course_discovery.domain.models import (
    CourseCandidate,
    EvidenceItem,
    SearchFilters,
    UserMemory,
)


def seed_cache(
    filters: SearchFilters,
    memory: UserMemory,
    *,
    limit: int,
) -> list[CourseCandidate]:
    topic_terms = {
        term
        for term in filters.topic.lower().replace("-", " ").split()
        if len(term) > 2 and term not in {"find", "free", "with", "for", "and", "the"}
    }
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
    candidates = [
        course
        for course in seed
        if course.url not in memory.completed_course_urls
        and course.url not in memory.rejected_course_urls
        and (course.provider or "") not in memory.avoided_providers
        and (
            not topic_terms
            or any(
                term in (course.title + " " + (course.description or "")).lower()
                for term in topic_terms
            )
        )
    ]
    return candidates[:limit]
