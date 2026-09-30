from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict

from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.privacy.erasure import erase_user


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m course_discovery.privacy")
    commands = parser.add_subparsers(dest="command", required=True)
    erase = commands.add_parser("erase", help="delete everything stored for one user")
    erase.add_argument("--user-id", required=True)
    erase.add_argument(
        "--execute", action="store_true", help="delete for real; the default is a dry run"
    )
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace) -> int:
    async with open_checkpointer() as saver:
        report = await erase_user(args.user_id, saver, execute=args.execute)
    print(json.dumps(asdict(report) | {"clean": report.clean if report.executed else None}, indent=2))
    if args.execute and not report.clean:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv

    load_dotenv()
    return asyncio.run(_run(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
