from __future__ import annotations

from datetime import datetime

from course_discovery.domain.contract import STATE_SCHEMA_VERSION, UNVERSIONED
from course_discovery.persistence.postgres import connect


class ThreadAccessError(PermissionError):
    pass


class IncompatibleThreadError(RuntimeError):
    def __init__(self, thread_id: str, stored: int, current: int):
        super().__init__(
            f"thread {thread_id} was written by state schema v{stored}; this code reads v{current}"
        )
        self.thread_id = thread_id
        self.stored = stored
        self.current = current


def register_thread(user_id: str | None, thread_id: str) -> None:
    if not user_id:
        return
    with connect() as conn:
        if conn is None:
            return
        with conn.transaction():
            conn.execute(
                """
                INSERT INTO run_threads (thread_id, user_id, schema_version) VALUES (%s, %s, %s)
                ON CONFLICT (thread_id) DO UPDATE SET last_activity_at = now()
                """,
                (thread_id, user_id, STATE_SCHEMA_VERSION),
            )


def authorize_thread(user_id: str | None, thread_id: str) -> None:
    with connect() as conn:
        if conn is None:
            return
        row = conn.execute(
            "SELECT user_id, schema_version FROM run_threads WHERE thread_id = %s", (thread_id,)
        ).fetchone()
    if not user_id or row is None or row[0] != user_id:
        raise ThreadAccessError("thread not found or not owned by caller")
    stored = row[1] if row[1] is not None else UNVERSIONED
    if stored != STATE_SCHEMA_VERSION:
        raise IncompatibleThreadError(thread_id, stored, STATE_SCHEMA_VERSION)


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
