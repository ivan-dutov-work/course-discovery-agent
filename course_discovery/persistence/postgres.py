from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator

from course_discovery.observability.logging import get_logger


logger = get_logger(__name__)


@contextmanager
def connect() -> Iterator[Any]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        yield None
        return

    try:
        import psycopg  # type: ignore[import-not-found]
    except ImportError:
        logger.warning(
            "postgres_driver_missing",
            extra={"event": "persistence.postgres_driver_missing"},
        )
        yield None
        return

    conn = psycopg.connect(database_url)
    try:
        yield conn
    finally:
        conn.close()
