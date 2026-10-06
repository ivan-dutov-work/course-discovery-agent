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
from course_discovery.observability.metrics import record_db_error
from course_discovery.persistence.postgres import connect
from course_discovery.research_agent.cache.scoring import (
    CONFIDENCE_WEIGHT,
    SIMILARITY_WEIGHT,
    USE_COUNT_CAP,
    USE_COUNT_WEIGHT,
    topic_floor,
    topic_relative_cutoff,
    topic_vector,
)
from course_discovery.research_agent.cache.seed_data import seed_cache
from course_discovery.research_agent.embeddings import course_text, embed_texts, to_pgvector


logger = get_logger(__name__)


def search_course_cache(
    filters: SearchFilters,
    memory: UserMemory,
    *,
    limit: int = 12,
    user_id: str | None = None,
) -> list[CourseCandidate]:
    query = topic_vector(filters.topic)
    with connect() as conn:
        if conn is None:
            return seed_cache(filters, memory, limit=limit)

        try:
            rows = conn.execute(
                """
                WITH matched AS (
                  SELECT c.id, s.sim, s.psim
                  FROM courses c
                  CROSS JOIN LATERAL (
                    SELECT CASE WHEN c.course_embedding IS NOT NULL
                                THEN 1 - (c.course_embedding <=> %(query)s::vector) END AS sim,
                           (SELECT 1 - (c.course_embedding <=> up.profile_embedding)
                            FROM user_preferences up
                            WHERE up.user_id = %(user_id)s
                              AND up.profile_embedding IS NOT NULL
                              AND c.course_embedding IS NOT NULL) AS psim
                  ) s
                  WHERE c.validation_status = 'valid'
                    AND (%(free_only)s = FALSE OR c.is_free = TRUE)
                    AND (%(certificate)s = FALSE OR c.has_certificate = TRUE)
                    AND (%(level)s = 'any' OR c.level = %(level)s OR c.level IS NULL)
                    AND (c.language = ANY(%(languages)s) OR c.language IS NULL)
                    AND NOT (c.canonical_url = ANY(%(completed)s))
                    AND NOT (c.canonical_url = ANY(%(rejected)s))
                    AND (s.sim IS NULL OR s.sim >= %(floor)s)
                )
                SELECT c.title, c.provider, c.canonical_url, c.description, c.price_text,
                       c.is_free, c.has_certificate, c.level, c.language, c.rating,
                       c.published_or_updated, c.validation_confidence,
                       m.psim, c.duration_hours,
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
                FROM matched m
                JOIN courses c ON c.id = m.id
                LEFT JOIN course_evidence e ON e.course_id = c.id
                WHERE m.sim IS NULL
                   OR m.sim >= %(relative)s * (SELECT max(sim) FROM matched)
                GROUP BY c.id, m.sim, m.psim
                ORDER BY (m.sim IS NULL),
                         (%(w_sim)s * COALESCE(m.sim, 0)
                          + %(w_conf)s * c.validation_confidence
                          + %(w_use)s * LEAST(c.use_count, %(use_cap)s) / %(use_cap)s::float) DESC,
                         c.id
                LIMIT %(limit)s
                """,
                {
                    "query": to_pgvector(query) if query is not None else None,
                    "user_id": user_id,
                    "free_only": filters.max_price == 0,
                    "certificate": filters.include_certificate,
                    "level": filters.level,
                    "languages": filters.content_languages,
                    "completed": memory.completed_course_urls,
                    "rejected": memory.rejected_course_urls,
                    "floor": topic_floor(),
                    "relative": topic_relative_cutoff(),
                    "w_sim": SIMILARITY_WEIGHT,
                    "w_conf": CONFIDENCE_WEIGHT,
                    "w_use": USE_COUNT_WEIGHT,
                    "use_cap": USE_COUNT_CAP,
                    "limit": limit,
                },
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            record_db_error("course_cache_search")
            logger.error(
                "course_cache_search_error",
                extra={"event": "persistence.course_cache_search_error", **sanitize_error(exc)},
            )
            raise

    candidates: list[CourseCandidate] = []
    for row in rows:
        if row[1] in memory.avoided_providers:
            continue
        evidence = [
            EvidenceItem.model_validate(item)
            for item in (row[14] if isinstance(row[14], list) else json.loads(row[14]))
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
                profile_similarity=float(row[12]) if row[12] is not None else None,
                duration_hours=float(row[13]) if row[13] is not None else None,
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
                stored: list[tuple[int, CourseCandidate, list[str]]] = []
                for course in courses:
                    validation = validation_by_url.get(course.url)
                    status = validation.status if validation else "uncertain"
                    row = conn.execute(
                        """
                        INSERT INTO courses (
                          canonical_url, title, provider, description, topics, level, language,
                          price_text, is_free, has_certificate, rating, published_or_updated,
                          last_seen_at, validation_status, validation_confidence, use_count,
                          duration_hours
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, 0, %s)
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
                          duration_hours = COALESCE(EXCLUDED.duration_hours, courses.duration_hours),
                          updated_at = now()
                        RETURNING id, topics
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
                            course.duration_hours,
                        ),
                    ).fetchone()
                    course_id = row[0]
                    stored.append((course_id, course, list(row[1] or [])))
                    for evidence in course.evidence:
                        conn.execute(
                            """
                            INSERT INTO course_evidence (
                              course_id, source_url, quote_or_summary, supports, observed_at,
                              source_type, confidence
                            )
                            VALUES (%s, %s, %s, %s, now(), %s, %s)
                            ON CONFLICT (course_id, source_url) DO UPDATE SET
                              quote_or_summary = EXCLUDED.quote_or_summary,
                              supports = EXCLUDED.supports,
                              observed_at = EXCLUDED.observed_at,
                              source_type = EXCLUDED.source_type,
                              confidence = EXCLUDED.confidence
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
                vectors = embed_texts(
                    [course_text(c.title, c.description, topics) for _, c, topics in stored]
                )
                for (course_id, _, _), vector in zip(stored, vectors):
                    conn.execute(
                        "UPDATE courses SET course_embedding = %s::vector WHERE id = %s",
                        (to_pgvector(vector), course_id),
                    )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            record_db_error("course_cache_upsert")
            logger.error(
                "course_cache_upsert_error",
                extra={"event": "persistence.course_cache_upsert_error", **sanitize_error(exc)},
            )
            raise


def stage_courses(
    run_id: str,
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
                    conn.execute(
                        """
                        INSERT INTO pending_courses (run_id, canonical_url, candidate, validation)
                        VALUES (%s, %s, %s::jsonb, %s::jsonb)
                        ON CONFLICT (run_id, canonical_url) DO UPDATE SET
                          candidate = EXCLUDED.candidate,
                          validation = EXCLUDED.validation
                        """,
                        (
                            run_id,
                            course.url,
                            course.model_dump_json(),
                            validation.model_dump_json() if validation else None,
                        ),
                    )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            record_db_error("course_cache_stage")
            logger.error(
                "course_cache_stage_error",
                extra={"event": "persistence.course_cache_stage_error", **sanitize_error(exc)},
            )
            raise


def promote_staged_courses(run_id: str, approved_urls: list[str]) -> int:
    with connect() as conn:
        if conn is None:
            return 0

        try:
            rows = conn.execute(
                """
                SELECT candidate, validation FROM pending_courses
                WHERE run_id = %s AND canonical_url = ANY(%s)
                ORDER BY canonical_url
                """,
                (run_id, approved_urls),
            ).fetchall()
        except Exception as exc:  # noqa: BLE001
            record_db_error("course_cache_promote")
            logger.error(
                "course_cache_promote_error",
                extra={"event": "persistence.course_cache_promote_error", **sanitize_error(exc)},
            )
            raise

    candidates = [CourseCandidate.model_validate(_json(row[0])) for row in rows]
    validations = [CandidateValidation.model_validate(_json(row[1])) for row in rows if row[1]]
    upsert_courses(candidates, validations)
    discard_staged_courses(run_id)
    return len(candidates)


def discard_staged_courses(run_id: str) -> None:
    with connect() as conn:
        if conn is None:
            return

        try:
            conn.execute("DELETE FROM pending_courses WHERE run_id = %s", (run_id,))
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            record_db_error("course_cache_discard")
            logger.error(
                "course_cache_discard_error",
                extra={"event": "persistence.course_cache_discard_error", **sanitize_error(exc)},
            )
            raise


def _json(value):
    return value if isinstance(value, dict) else json.loads(value)
