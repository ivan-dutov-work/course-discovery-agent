from __future__ import annotations

import argparse
import sys

from course_discovery.persistence.postgres import connect
from course_discovery.research_agent.embeddings.base import (
    course_text,
    embed_texts,
    to_pgvector,
)


def backfill(batch_size: int = 200, *, everything: bool = False) -> int:
    filled = 0
    last_id = 0
    with connect() as conn:
        if conn is None:
            raise SystemExit("DATABASE_URL is not set")
        while True:
            with conn.transaction():
                rows = conn.execute(
                    """
                    SELECT id, title, description, topics FROM courses
                    WHERE id > %s AND (%s OR course_embedding IS NULL)
                    ORDER BY id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (last_id, everything, batch_size),
                ).fetchall()
                if not rows:
                    return filled
                vectors = embed_texts([course_text(r[1], r[2], list(r[3] or [])) for r in rows])
                for row, vector in zip(rows, vectors):
                    conn.execute(
                        "UPDATE courses SET course_embedding = %s::vector WHERE id = %s",
                        (to_pgvector(vector), row[0]),
                    )
                filled += len(rows)
                last_id = rows[-1][0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="course_discovery.research_agent.embeddings")
    parser.add_argument("command", choices=["backfill"])
    parser.add_argument("--all", action="store_true", help="re-embed rows that already have a vector")
    args = parser.parse_args(argv)
    print(f"backfilled {backfill(everything=args.all)} courses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
