from __future__ import annotations

import threading
import unittest

from outbox_contract import OutboxContract, Recorder, effect

from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.models import RecordStatus
from course_discovery.effects.worker import OutboxWorker


class InMemoryOutboxTests(OutboxContract, unittest.TestCase):
    def make_store(self):
        return InMemoryOutboxStore()

    def test_run_forever_stops_when_signalled_and_drains_queue(self):
        stop = threading.Event()
        handler = Recorder()
        self.store.enqueue(effect(), self.clock())
        worker = OutboxWorker(
            self.store, {"publish_digest": handler}, clock=self.clock
        )

        original = worker.run_once

        def run_once_then_stop():
            stats = original()
            stop.set()
            return stats

        worker.run_once = run_once_then_stop
        worker.run_forever(stop, poll_seconds=0)

        self.assertEqual(handler.delivered, ["publish:run-1"])
        self.assertEqual(self.store.get("publish:run-1").status, RecordStatus.DELIVERED)


if __name__ == "__main__":
    unittest.main()
