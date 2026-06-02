from __future__ import annotations

from course_discovery.domain.models import CourseCandidate, UserMemory
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.persistence.postgres import connect


logger = get_logger(__name__)


def load_user_memory(user_id: str | None) -> UserMemory:
    if not user_id:
        return UserMemory()

    with connect() as conn:
        if conn is None:
            return UserMemory()

        try:
            row = conn.execute(
                """
                SELECT preferred_providers, avoided_providers, preferred_languages,
                       budget_preference, certificate_importance, preferred_level,
                       learning_style_notes, career_goals, raw_memory_json
                FROM user_preferences
                WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()
            if row is None:
                return UserMemory()

            raw_memory = row[8] or {}
            return UserMemory(
                preferred_providers=row[0] or [],
                avoided_providers=row[1] or [],
                preferred_languages=row[2] or [],
                budget_preference=row[3],
                certificate_importance=row[4],
                preferred_level=row[5],
                learning_style_notes=row[6],
                career_goals=row[7] or [],
                completed_course_urls=raw_memory.get("completed_course_urls", []),
                rejected_course_urls=raw_memory.get("rejected_course_urls", []),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "user_memory_load_error",
                extra={"event": "persistence.user_memory_load_error", **sanitize_error(exc)},
            )
            return UserMemory()


def record_feedback(
    user_id: str | None,
    courses: list[CourseCandidate],
    query: str,
    *,
    accepted: bool,
    feedback_text: str | None,
) -> None:
    if not user_id or not courses:
        return
    with connect() as conn:
        if conn is None:
            return
        try:
            with conn.transaction():
                for rank, course in enumerate(courses, start=1):
                    course_row = conn.execute(
                        "SELECT id FROM courses WHERE canonical_url = %s",
                        (course.url,),
                    ).fetchone()
                    if course_row is None:
                        continue
                    conn.execute(
                        """
                        INSERT INTO recommendation_events (
                          user_id, course_id, query, rank, recommendation_reason,
                          accepted, rejected, feedback_text
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            user_id,
                            course_row[0],
                            query,
                            rank,
                            "published recommendation" if accepted else "review feedback",
                            accepted,
                            not accepted,
                            feedback_text,
                        ),
                    )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "feedback_record_error",
                extra={"event": "persistence.feedback_record_error", **sanitize_error(exc)},
            )
