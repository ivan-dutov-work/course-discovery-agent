from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def main() -> int:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True) as conn:
        for path in sorted(MIGRATIONS.glob("*.sql")):
            conn.execute(path.read_text())
            print(f"applied {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
