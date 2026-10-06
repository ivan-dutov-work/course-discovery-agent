from __future__ import annotations

import os
import signal
import sys
import threading

from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.guardrails.injection import set_injection_screen
from course_discovery.jobs.tagging import TAG_COURSE, tagging_handler


class _PassScreen:
    def score(self, text: str) -> float:
        return 0.0


class DyingTagger:
    def __init__(self, log_path: str, die_after: int) -> None:
        self.log_path = log_path
        self.die_after = die_after
        self.calls = 0

    def tag(self, title: str, description: str) -> list[str]:
        with open(self.log_path, "a") as log:
            log.write(title + "\n")
        self.calls += 1
        if self.calls == self.die_after:
            os.kill(os.getpid(), signal.SIGKILL)
        return ["python"]


def main(log_path: str, die_after: str) -> None:
    url = os.environ["DATABASE_URL"]
    set_injection_screen(_PassScreen())
    store = PostgresOutboxStore(url)
    worker = OutboxWorker(
        store, {TAG_COURSE: tagging_handler(url, DyingTagger(log_path, int(die_after)))}, lease_seconds=1.0, batch_size=5
    )
    worker.run_forever(threading.Event(), poll_seconds=0.1)


if __name__ == "__main__":
    main(*sys.argv[1:])
