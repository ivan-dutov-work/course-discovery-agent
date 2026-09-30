from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from psycopg import sql


@dataclass(frozen=True)
class UserDataSource:
    table: str
    user_column: str
    json_key: str | None = None
    breakdown_column: str | None = None

    def _match(self) -> sql.Composable:
        column = sql.Identifier(self.user_column)
        if self.json_key is None:
            return column
        return sql.SQL("{} ->> {}").format(column, sql.Literal(self.json_key))

    def count(self, conn: Any, user_id: str) -> tuple[int, dict[str, int]]:
        table = sql.Identifier(self.table)
        if self.breakdown_column is None:
            query = sql.SQL("SELECT count(*) FROM {} WHERE {} = %s").format(table, self._match())
            return conn.execute(query, (user_id,)).fetchone()[0], {}
        breakdown = sql.Identifier(self.breakdown_column)
        query = sql.SQL("SELECT {b}, count(*) FROM {t} WHERE {m} = %s GROUP BY {b}").format(
            b=breakdown, t=table, m=self._match()
        )
        by_value = {str(value): count for value, count in conn.execute(query, (user_id,))}
        return sum(by_value.values()), by_value

    def delete(self, conn: Any, user_id: str) -> None:
        query = sql.SQL("DELETE FROM {} WHERE {} = %s").format(
            sql.Identifier(self.table), self._match()
        )
        conn.execute(query, (user_id,))


# Rows that reference `users` go first: their foreign keys are ON DELETE SET NULL, so they
# would otherwise outlive the user with their content intact.
USER_DATA_SOURCES = (
    UserDataSource("outbox", "payload", json_key="user_id", breakdown_column="status"),
    UserDataSource("recommendation_events", "user_id"),
    UserDataSource("research_runs", "user_id"),
    UserDataSource("user_preferences", "user_id"),
    UserDataSource("users", "id"),
    UserDataSource("run_threads", "user_id"),
)

NOT_USER_DATA = frozenset(
    {
        "courses",
        "course_evidence",
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
)
