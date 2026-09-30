from __future__ import annotations

import argparse
import sys

from course_discovery.persistence.postgres import connect
from course_discovery.research_agent.embeddings.base import (
    course_text,
    embed_texts,
    to_pgvector,
)


def backfill(batch_size: int = 200) -> int:
    filled = 0
    with connect() as conn:
        if conn is None:
            raise SystemExit("DATABASE_URL is not set")
        while True:
            with conn.transaction():
                rows = conn.execute(
                    """
                    SELECT id, title, description, topics FROM courses
                    WHERE course_embedding IS NULL
                    ORDER BY id
                    LIMIT %s
                    FOR UPDATE SKIP LOCKED
                    """,
                    (batch_size,),
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="course_discovery.research_agent.embeddings")
    parser.add_argument("command", choices=["backfill"])
    parser.parse_args(argv)
    print(f"backfilled {backfill()} courses")
    return 0


if __name__ == "__main__":
    sys.exit(main())
