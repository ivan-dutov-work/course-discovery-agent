from __future__ import annotations

import argparse
import asyncio
import os
import signal
import threading

from course_discovery.effects.handlers import default_handlers
from course_discovery.effects.maintenance import list_dead, prune_outbox, requeue
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker, utcnow
from course_discovery.observability.logging import configure_logging
from course_discovery.observability.metrics import (
    configure_metrics,
    register_outbox_gauges,
    shutdown_metrics,
)
from course_discovery.observability.tracing import configure_tracing, shutdown_tracing
from course_discovery.persistence.checkpointer import open_checkpointer, prune_checkpoints


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m course_discovery.effects")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("worker")
    dead = commands.add_parser("dead")
    dead.add_argument("--limit", type=int, default=50)
    requeue_cmd = commands.add_parser("requeue")
    requeue_cmd.add_argument("key")
    prune = commands.add_parser("prune")
    prune.add_argument("--older-than-days", type=float, default=30.0)
    prune.add_argument("--checkpoints", action="store_true")
    return parser


def _run_worker(store: PostgresOutboxStore) -> None:
    worker = OutboxWorker(store, default_handlers())
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    configure_tracing("course-agent-outbox-worker")
    configure_metrics("course-agent-outbox-worker")
    register_outbox_gauges(lambda: store.stats(utcnow()))
    try:
        worker.run_forever(stop)
    finally:
        shutdown_tracing()
        shutdown_metrics()


async def _prune_checkpoints(days: float) -> list[str]:
    async with open_checkpointer() as saver:
        return await prune_checkpoints(saver, days)


def main(argv: list[str] | None = None) -> None:
    configure_logging()
    args = _parser().parse_args(argv)
    store = PostgresOutboxStore(os.environ["DATABASE_URL"])
    command = args.command or "worker"

    if command == "worker":
        _run_worker(store)
    elif command == "dead":
        for record in list_dead(store, args.limit):
            print(f"{record.effect.key}\t{record.effect.kind}\tattempts={record.attempts}\t{record.last_error}")
    elif command == "requeue":
        if not requeue(store, args.key):
            raise SystemExit(f"no dead record with key {args.key!r}")
        print(f"requeued {args.key}")
    elif command == "prune":
        print(f"deleted {prune_outbox(store, args.older_than_days)} delivered outbox rows")
        if args.checkpoints:
            threads = asyncio.run(_prune_checkpoints(args.older_than_days))
            print(f"deleted checkpoints for {len(threads)} threads")


if __name__ == "__main__":
    main()
