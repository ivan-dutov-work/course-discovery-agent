from __future__ import annotations

import argparse
import os

from course_discovery.effects.gateway import OutboxGateway
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.jobs.tagging import schedule_tagging


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m course_discovery.jobs")
    commands = parser.add_subparsers(dest="command", required=True)
    tag = commands.add_parser("tag-schedule")
    tag.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.command == "tag-schedule":
        url = os.environ["DATABASE_URL"]
        submitted = schedule_tagging(url, OutboxGateway(PostgresOutboxStore(url)), limit=args.limit)
        print(f"submitted {submitted} tagging effects")


if __name__ == "__main__":
    main()
