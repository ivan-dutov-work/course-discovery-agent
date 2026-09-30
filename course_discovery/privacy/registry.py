from __future__ import annotations

from datetime import datetime

from course_discovery.persistence.postgres import connect


def register_thread(user_id: str | None, thread_id: str) -> None:
    if not user_id:
        return
    with connect() as conn:
        if conn is None:
            return
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO run_threads (thread_id, user_id) VALUES (%s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET last_activity_at = now()
                """,
                (thread_id, user_id),
            )


def stale_threads(cutoff: datetime) -> list[str]:
    with connect() as conn:
        if conn is None:
            return []
        rows = conn.execute(
            "SELECT thread_id FROM run_threads WHERE last_activity_at < %s", (cutoff,)
        )
        return [row[0] for row in rows]


def forget_threads(thread_ids: list[str]) -> None:
    if not thread_ids:
        return
    with connect() as conn:
        if conn is None:
            return
        with conn.transaction():
            conn.execute("DELETE FROM run_threads WHERE thread_id = ANY(%s)", (thread_ids,))
