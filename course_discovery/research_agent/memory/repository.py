from __future__ import annotations

from psycopg.types.json import Jsonb

from course_discovery.domain.models import (
    LIST_MEMORY_FIELDS,
    CourseCandidate,
    MemoryNote,
    MemoryPatch,
    UserMemory,
)
from course_discovery.guardrails import redact_pii
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.observability.metrics import record_db_error
from course_discovery.persistence.postgres import connect
from course_discovery.research_agent.embeddings import to_pgvector
from course_discovery.research_agent.memory.profile_vector import profile_vector


logger = get_logger(__name__)

MAX_NOTES = 30


_SELECT_PREFERENCES = """
    SELECT preferred_providers, avoided_providers, preferred_languages,
           budget_preference, certificate_importance, preferred_level,
           learning_style_notes, career_goals, raw_memory_json, preferred_course_length
    FROM user_preferences
    WHERE user_id = %s
"""


def _row_to_memory(row) -> UserMemory:
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
        notes=[MemoryNote.model_validate(note) for note in raw_memory.get("notes", [])],
        preferred_course_length=row[9],
    )


def load_user_memory(user_id: str | None) -> UserMemory:
    if not user_id:
        return UserMemory()

    with connect() as conn:
        if conn is None:
            return UserMemory()

        try:
            row = conn.execute(_SELECT_PREFERENCES, (user_id,)).fetchone()
            return UserMemory() if row is None else _row_to_memory(row)
        except Exception as exc:  # noqa: BLE001
            record_db_error("user_memory_load")
            logger.error(
                "user_memory_load_error",
                extra={"event": "persistence.user_memory_load_error", **sanitize_error(exc)},
            )
            raise


def _merge_unique(current: list[str], add: list[str], remove: list[str]) -> list[str]:
    removed = set(remove)
    merged = [item for item in current if item not in removed]
    merged.extend(item for item in add if item not in merged and item not in removed)
    return merged


def apply_patch(memory: UserMemory, patch: MemoryPatch) -> UserMemory:
    updates: dict[str, object] = dict(patch.set)
    for name in LIST_MEMORY_FIELDS:
        if name in patch.add or name in patch.remove:
            updates[name] = _merge_unique(
                getattr(memory, name), patch.add.get(name, []), patch.remove.get(name, [])
            )
    if patch.add_notes:
        known = {(note.text, note.scope) for note in memory.notes}
        fresh = [note for note in patch.add_notes if (note.text, note.scope) not in known]
        updates["notes"] = [*memory.notes, *fresh][-MAX_NOTES:]
    return memory.model_copy(update=updates)


def redact_patch(patch: MemoryPatch) -> MemoryPatch:
    return MemoryPatch(
        set={
            name: redact_pii(value) if name == "learning_style_notes" else value
            for name, value in patch.set.items()
        },
        add={
            name: [redact_pii(item) or "" for item in items] if name == "career_goals" else items
            for name, items in patch.add.items()
        },
        remove=patch.remove,
        add_notes=[
            note.model_copy(update={"text": redact_pii(note.text) or ""}) for note in patch.add_notes
        ],
    )


def memory_update_exists(run_id: str) -> bool:
    with connect() as conn:
        if conn is None:
            return False
        return conn.execute("SELECT 1 FROM memory_updates WHERE run_id = %s", (run_id,)).fetchone() is not None


def save_user_memory(
    user_id: str | None, patch: MemoryPatch, *, run_id: str | None = None
) -> UserMemory | None:
    if not user_id or patch.is_empty():
        return None
    patch = redact_patch(patch)
    with connect() as conn:
        if conn is None:
            return None
        try:
            with conn.transaction():
                if run_id is not None:
                    claimed = conn.execute(
                        """
                        INSERT INTO memory_updates (run_id, user_id, patch) VALUES (%s, %s, %s)
                        ON CONFLICT (run_id) DO NOTHING RETURNING run_id
                        """,
                        (run_id, user_id, Jsonb(patch.model_dump(mode="json"))),
                    ).fetchone()
                    if claimed is None:
                        return None
                conn.execute(
                    "INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                    (user_id,),
                )
                conn.execute(
                    "INSERT INTO user_preferences (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
                    (user_id,),
                )
                row = conn.execute(_SELECT_PREFERENCES + " FOR UPDATE", (user_id,)).fetchone()
                merged = apply_patch(_row_to_memory(row), patch)
                vector = profile_vector(merged)
                raw_memory = dict(row[8] or {})
                raw_memory.update(
                    completed_course_urls=merged.completed_course_urls,
                    rejected_course_urls=merged.rejected_course_urls,
                    notes=[note.model_dump(mode="json") for note in merged.notes],
                )
                conn.execute(
                    """
                    UPDATE user_preferences
                    SET preferred_providers = %s, avoided_providers = %s,
                        preferred_languages = %s, budget_preference = %s,
                        certificate_importance = %s, preferred_level = %s,
                        preferred_course_length = %s, learning_style_notes = %s,
                        career_goals = %s, raw_memory_json = %s,
                        profile_embedding = %s::vector, updated_at = now()
                    WHERE user_id = %s
                    """,
                    (
                        merged.preferred_providers,
                        merged.avoided_providers,
                        merged.preferred_languages,
                        merged.budget_preference,
                        merged.certificate_importance,
                        merged.preferred_level,
                        merged.preferred_course_length,
                        merged.learning_style_notes,
                        merged.career_goals,
                        Jsonb(raw_memory),
                        to_pgvector(vector) if vector is not None else None,
                        user_id,
                    ),
                )
            return merged
        except Exception as exc:  # noqa: BLE001
            record_db_error("user_memory_save")
            logger.error(
                "user_memory_save_error",
                extra={"event": "persistence.user_memory_save_error", **sanitize_error(exc)},
            )
            raise


def record_feedback(
    user_id: str | None,
    courses: list[CourseCandidate],
    query: str,
    *,
    accepted: bool,
    feedback_text: str | None,
    run_id: str | None = None,
) -> None:
    if not user_id or not courses:
        return
    query = redact_pii(query) or ""
    feedback_text = redact_pii(feedback_text)
    with connect() as conn:
        if conn is None:
            return
        try:
            with conn.transaction():
                conn.execute(
                    "INSERT INTO users (id) VALUES (%s) ON CONFLICT (id) DO NOTHING",
                    (user_id,),
                )
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
                          accepted, rejected, feedback_text, idempotency_key
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (idempotency_key) DO NOTHING
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
                            f"{run_id}:{course.url}" if run_id else None,
                        ),
                    )
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            record_db_error("feedback_record")
            logger.error(
                "feedback_record_error",
                extra={"event": "persistence.feedback_record_error", **sanitize_error(exc)},
            )
            raise
