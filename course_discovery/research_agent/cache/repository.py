from __future__ import annotations

import json

from course_discovery.domain.models import (
    CandidateValidation,
    CourseCandidate,
    EvidenceItem,
    SearchFilters,
    UserMemory,
)
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.persistence.postgres import connect
from course_discovery.research_agent.cache.seed_data import seed_cache


logger = get_logger(__name__)


def search_course_cache(
    filters: SearchFilters,
    memory: UserMemory,
    *,
    limit: int = 12,
) -> list[CourseCandidate]:
    with connect() as conn:
        if conn is None:
            return seed_cache(filters, memory, limit=limit)

        try:
            rows = conn.execute(
                """
                SELECT c.title, c.provider, c.canonical_url, c.description, c.price_text,
                       c.is_free, c.has_certificate, c.level, c.language, c.rating,
                       c.published_or_updated, c.validation_confidence,
                       COALESCE(
                         jsonb_agg(
                           jsonb_build_object(
                             'source_url', e.source_url,
                             'quote_or_summary', e.quote_or_summary,
                             'supports', e.supports
                           )
                         ) FILTER (WHERE e.id IS NOT NULL),
                         '[]'::jsonb
                       ) AS evidence
                FROM courses c
                LEFT JOIN course_evidence e ON e.course_id = c.id
                WHERE c.validation_status = 'valid'
                  AND (%s = FALSE OR c.is_free = TRUE)
                  AND (%s = FALSE OR c.has_certificate = TRUE)
                  AND (%s = 'any' OR c.level = %s OR c.level IS NULL)
                  AND (c.language = ANY(%s) OR c.language IS NULL)
                  AND NOT (c.canonical_url = ANY(%s))
                  AND NOT (c.canonical_url = ANY(%s))
                GROUP BY c.id
                ORDER BY c.use_count DESC, c.validation_confidence DESC, c.last_seen_at DESC
                LIMIT %s
                """,
                (
                    filters.max_price == 0,
                    filters.include_certificate,
                    filters.level,
                    filters.level,
                    filters.content_languages,
                    memory.completed_course_urls,
                    memory.rejected_course_urls,
                    limit,
                ),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "course_cache_search_error",
                extra={"event": "persistence.course_cache_search_error", **sanitize_error(exc)},
            )
            return []

    candidates: list[CourseCandidate] = []
    for row in rows:
        if row[1] in memory.avoided_providers:
            continue
        evidence = [
            EvidenceItem.model_validate(item)
            for item in (row[12] if isinstance(row[12], list) else json.loads(row[12]))
        ]
        candidates.append(
            CourseCandidate(
                title=row[0],
                provider=row[1],
                url=row[2],
                description=row[3],
                price=row[4],
                is_free=row[5],
                has_certificate=row[6],
                level=row[7],
                language=row[8],
                rating=row[9],
                published_or_updated=str(row[10]) if row[10] else None,
                source="cache",
                evidence=evidence,
                confidence=float(row[11] or 0.7),
            )
        )
    return candidates


def upsert_courses(
    courses: list[CourseCandidate],
    validations: list[CandidateValidation],
) -> None:
    if not courses:
        return
    validation_by_url = {item.url: item for item in validations}
    with connect() as conn:
        if conn is None:
            return

        try:
            with conn.transaction():
                for course in courses:
                    validation = validation_by_url.get(course.url)
                    status = validation.status if validation else "uncertain"
                    row = conn.execute(
                        """
                        INSERT INTO courses (
                          canonical_url, title, provider, description, topics, level, language,
                          price_text, is_free, has_certificate, rating, published_or_updated,
                          last_seen_at, validation_status, validation_confidence, use_count
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, 0)
                        ON CONFLICT (canonical_url) DO UPDATE SET
                          title = EXCLUDED.title,
                          provider = EXCLUDED.provider,
                          description = EXCLUDED.description,
                          level = EXCLUDED.level,
                          language = EXCLUDED.language,
                          price_text = EXCLUDED.price_text,
                          is_free = EXCLUDED.is_free,
                          has_certificate = EXCLUDED.has_certificate,
                          rating = EXCLUDED.rating,
                          published_or_updated = EXCLUDED.published_or_updated,
                          last_seen_at = now(),
                          validation_status = EXCLUDED.validation_status,
                          validation_confidence = EXCLUDED.validation_confidence,
                          updated_at = now()
                        RETURNING id
                        """,
                        (
                            course.url,
                            course.title,
                            course.provider,
                            course.description,
                            [],
                            course.level,
                            course.language,
                            course.price,
                            course.is_free,
                            course.has_certificate,
                            course.rating,
                            course.published_or_updated,
                            status,
                            course.confidence,
                        ),
                    ).fetchone()
                    course_id = row[0]
                    for evidence in course.evidence:
                        conn.execute(
                            """
                            INSERT INTO course_evidence (
                              course_id, source_url, quote_or_summary, supports, observed_at,
                              source_type, confidence
                            )
                            VALUES (%s, %s, %s, %s, now(), %s, %s)
                            """,
                            (
                                course_id,
                                evidence.source_url,
                                evidence.quote_or_summary,
                                evidence.supports,
                                course.source,
                                course.confidence,
                            ),
                        )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "course_cache_upsert_error",
                extra={"event": "persistence.course_cache_upsert_error", **sanitize_error(exc)},
            )
