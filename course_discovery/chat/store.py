from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import psycopg

from course_discovery.chat.transport import InboundMessage

MAX_ATTEMPTS = 3


class LeaseLost(RuntimeError):
    pass


@dataclass(frozen=True)
class Batch:
    user_id: str
    batch_id: int
    update_ids: tuple[int, ...]
    text: str
    attempts: int
    owner: str

    @property
    def thread_id(self) -> str:
        return f"chat-{self.batch_id}"


class ChatStore:
    def __init__(self, database_url: str, lease_seconds: float = 120.0) -> None:
        self.database_url = database_url
        self.lease_seconds = lease_seconds

    def _connect(self) -> Any:
        return psycopg.connect(self.database_url)

    def ingest(self, message: InboundMessage, redacted_text: str) -> bool:
        with self._connect() as conn, conn.transaction():
            inserted = conn.execute(
                "INSERT INTO chat_updates (update_id) VALUES (%s) ON CONFLICT DO NOTHING RETURNING update_id",
                (message.update_id,),
            ).fetchone()
            if inserted is None:
                return False
            conn.execute(
                "INSERT INTO chat_identities (chat_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (message.chat_id, f"tg-{uuid.uuid4().hex}"),
            )
            user_id = conn.execute(
                "SELECT user_id FROM chat_identities WHERE chat_id = %s", (message.chat_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO chat_inbox (update_id, user_id, text) VALUES (%s, %s, %s)",
                (message.update_id, user_id, redacted_text),
            )
            return True

    def chat_id_for(self, user_id: str) -> int | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT chat_id FROM chat_identities WHERE user_id = %s", (user_id,)
            ).fetchone()
        return row[0] if row else None

    def claim(self, owner: str) -> Batch | None:
        with self._connect() as conn:
            users = [
                row[0]
                for row in conn.execute(
                    "SELECT user_id FROM chat_inbox WHERE done_at IS NULL "
                    "GROUP BY user_id ORDER BY min(update_id)"
                )
            ]
            conn.commit()
            for user_id in users:
                batch = self._claim_user(conn, user_id, owner)
                if batch is not None:
                    return batch
        return None

    def _claim_user(self, conn: Any, user_id: str, owner: str) -> Batch | None:
        with conn.transaction():
            leased = conn.execute(
                """
                INSERT INTO chat_user_leases (user_id, owner, locked_until)
                VALUES (%s, %s, now() + make_interval(secs => %s))
                ON CONFLICT (user_id) DO UPDATE
                    SET owner = EXCLUDED.owner, locked_until = EXCLUDED.locked_until
                    WHERE chat_user_leases.locked_until < now()
                RETURNING user_id
                """,
                (user_id, owner, self.lease_seconds),
            ).fetchone()
            if leased is None:
                return None
            rows = conn.execute(
                "SELECT update_id, text, batch_id, attempts FROM chat_inbox "
                "WHERE user_id = %s AND done_at IS NULL AND batch_id IS NOT NULL ORDER BY update_id",
                (user_id,),
            ).fetchall()
            if not rows:
                rows = conn.execute(
                    "SELECT update_id, text, batch_id, attempts FROM chat_inbox "
                    "WHERE user_id = %s AND done_at IS NULL AND batch_id IS NULL ORDER BY update_id",
                    (user_id,),
                ).fetchall()
                if not rows:
                    conn.execute("DELETE FROM chat_user_leases WHERE user_id = %s", (user_id,))
                    return None
            batch_id = rows[0][2] if rows[0][2] is not None else rows[0][0]
            ids = [row[0] for row in rows]
            conn.execute(
                "UPDATE chat_inbox SET batch_id = %s, attempts = attempts + 1 WHERE update_id = ANY(%s)",
                (batch_id, ids),
            )
            return Batch(
                user_id=user_id,
                batch_id=batch_id,
                update_ids=tuple(ids),
                text="\n".join(row[1] for row in rows),
                attempts=max(row[3] for row in rows) + 1,
                owner=owner,
            )

    def renew(self, batch: Batch) -> None:
        with self._connect() as conn, conn.transaction():
            renewed = conn.execute(
                "UPDATE chat_user_leases SET locked_until = now() + make_interval(secs => %s) "
                "WHERE user_id = %s AND owner = %s RETURNING user_id",
                (self.lease_seconds, batch.user_id, batch.owner),
            ).fetchone()
        if renewed is None:
            raise LeaseLost(batch.user_id)

    def complete(self, batch: Batch) -> None:
        with self._connect() as conn, conn.transaction():
            held = conn.execute(
                "SELECT 1 FROM chat_user_leases WHERE user_id = %s AND owner = %s FOR UPDATE",
                (batch.user_id, batch.owner),
            ).fetchone()
            if held is None:
                raise LeaseLost(batch.user_id)
            conn.execute(
                "UPDATE chat_inbox SET done_at = now() WHERE update_id = ANY(%s)",
                (list(batch.update_ids),),
            )
            conn.execute("DELETE FROM chat_user_leases WHERE user_id = %s", (batch.user_id,))
