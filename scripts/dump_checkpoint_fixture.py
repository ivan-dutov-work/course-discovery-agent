from __future__ import annotations

import base64
import json
import os
import sys

import psycopg
from psycopg.rows import dict_row

TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
ALLOWED_PREFIXES = ("fixture-", "legacy-")


def _encode(value):
    if isinstance(value, (bytes, memoryview)):
        return {"b64": base64.b64encode(bytes(value)).decode()}
    return value


def main(thread_id: str, out_path: str) -> None:
    if not thread_id.startswith(ALLOWED_PREFIXES):
        raise SystemExit(f"refusing to dump {thread_id!r}: thread ids must start with {ALLOWED_PREFIXES}")
    with psycopg.connect(os.environ["TEST_DATABASE_URL"], row_factory=dict_row) as conn:
        dump = {
            table: [
                {k: _encode(v) for k, v in row.items()}
                for row in conn.execute(f"SELECT * FROM {table} WHERE thread_id = %s", (thread_id,))
            ]
            for table in TABLES
        }
    with open(out_path, "w") as handle:
        json.dump(dump, handle, indent=1, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
