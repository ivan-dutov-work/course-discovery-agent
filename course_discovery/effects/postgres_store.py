from __future__ import annotations

from datetime import datetime, timedelta

from psycopg.types.json import Jsonb

from course_discovery.effects.models import Effect, OutboxRecord, OutboxStats, RecordStatus
from course_discovery.persistence.postgres import open_connection


_COLUMNS = "key, kind, payload, status, attempts, next_attempt_at, locked_until, last_error, delivered_at, created_at"


def _record(row) -> OutboxRecord:
    return OutboxRecord(
        effect=Effect(key=row[0], kind=row[1], payload=row[2]),
        status=RecordStatus(row[3]),
        attempts=row[4],
        next_attempt_at=row[5],
        locked_until=row[6],
        last_error=row[7],
        delivered_at=row[8],
        created_at=row[9],
    )


class PostgresOutboxStore:
    def __init__(self, database_url: str) -> None:
        self.database_url = database_url

    def enqueue(self, effect: Effect, now: datetime) -> OutboxRecord:
        with open_connection(self.database_url) as conn:
            conn.execute(
                """
                INSERT INTO outbox (key, kind, payload, next_attempt_at, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (key) DO NOTHING
                """,
                (effect.key, effect.kind, Jsonb(effect.payload), now, now),
            )
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM outbox WHERE key = %s", (effect.key,)
            ).fetchone()
        return _record(row)

    def claim(self, now: datetime, limit: int, lease_seconds: float) -> list[OutboxRecord]:
        with open_connection(self.database_url) as conn:
            rows = conn.execute(
                f"""
                UPDATE outbox
                SET status = 'in_progress',
                    attempts = attempts + 1,
                    locked_until = %s
                WHERE id IN (
                    SELECT id FROM outbox
                    WHERE (status = 'queued' AND next_attempt_at <= %s)
                       OR (status = 'in_progress' AND locked_until <= %s)
                    ORDER BY next_attempt_at, id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING {_COLUMNS}
                """,
                (now + timedelta(seconds=lease_seconds), now, now, limit),
            ).fetchall()
        return sorted((_record(row) for row in rows), key=lambda r: (r.next_attempt_at, r.effect.key))

    def mark_delivered(self, key: str, now: datetime) -> None:
        self._execute(
            """
            UPDATE outbox
            SET status = 'delivered', locked_until = NULL, last_error = NULL, delivered_at = %s
            WHERE key = %s
            """,
            (now, key),
        )

    def release(self, key: str, error: str, retry_at: datetime) -> None:
        self._execute(
            """
            UPDATE outbox
            SET status = 'queued', locked_until = NULL, last_error = %s, next_attempt_at = %s
            WHERE key = %s
            """,
            (error, retry_at, key),
        )

    def mark_dead(self, key: str, error: str) -> None:
        self._execute(
            "UPDATE outbox SET status = 'dead', locked_until = NULL, last_error = %s WHERE key = %s",
            (error, key),
        )

    def get(self, key: str) -> OutboxRecord | None:
        with open_connection(self.database_url) as conn:
            row = conn.execute(
                f"SELECT {_COLUMNS} FROM outbox WHERE key = %s", (key,)
            ).fetchone()
        return _record(row) if row else None

    def list_dead(self, limit: int) -> list[OutboxRecord]:
        with open_connection(self.database_url) as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM outbox WHERE status = 'dead' ORDER BY key LIMIT %s",
                (limit,),
            ).fetchall()
        return [_record(row) for row in rows]

    def requeue(self, key: str, now: datetime) -> bool:
        with open_connection(self.database_url) as conn:
            cursor = conn.execute(
                """
                UPDATE outbox
                SET status = 'queued', attempts = 0, next_attempt_at = %s
                WHERE key = %s AND status = 'dead'
                """,
                (now, key),
            )
            return cursor.rowcount == 1

    def prune_delivered(self, older_than: datetime) -> int:
        with open_connection(self.database_url) as conn:
            cursor = conn.execute(
                "DELETE FROM outbox WHERE status = 'delivered' AND delivered_at < %s",
                (older_than,),
            )
            return cursor.rowcount

    def stats(self, now: datetime) -> OutboxStats:
        with open_connection(self.database_url) as conn:
            pending, dead, oldest = conn.execute(
                """
                SELECT count(*) FILTER (WHERE status IN ('queued', 'in_progress')),
                       count(*) FILTER (WHERE status = 'dead'),
                       min(created_at) FILTER (WHERE status IN ('queued', 'in_progress'))
                FROM outbox
                """
            ).fetchone()
        return OutboxStats(
            pending=pending,
            dead=dead,
            oldest_pending_age_seconds=(now - oldest).total_seconds() if oldest else 0.0,
        )

    def _execute(self, sql: str, params: tuple) -> None:
        with open_connection(self.database_url) as conn:
            conn.execute(sql, params)
