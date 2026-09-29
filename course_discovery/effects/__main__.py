from __future__ import annotations

import os
import signal
import threading

from course_discovery.effects.handlers import default_handlers
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.observability.logging import configure_logging


def main() -> None:
    configure_logging()
    database_url = os.environ["DATABASE_URL"]
    worker = OutboxWorker(PostgresOutboxStore(database_url), default_handlers())
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    worker.run_forever(stop)


if __name__ == "__main__":
    main()
