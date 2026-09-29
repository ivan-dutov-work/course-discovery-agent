from __future__ import annotations

import os
import threading
import unittest
from datetime import timedelta
from pathlib import Path

import psycopg
from outbox_contract import OutboxContract, Recorder, effect

from course_discovery.effects.models import RecordStatus
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
MIGRATION = Path(__file__).parent.parent / "migrations" / "003_outbox.sql"


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class PostgresOutboxTests(OutboxContract, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(MIGRATION.read_text())

    def make_store(self):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute("TRUNCATE outbox")
        return PostgresOutboxStore(TEST_DATABASE_URL)

    def test_parallel_workers_deliver_each_effect_exactly_once(self):
        keys = [f"publish:run-{index}" for index in range(40)]
        for key in keys:
            self.store.enqueue(effect(key), self.clock())
        handler = Recorder()
        barrier = threading.Barrier(4)

        def drain():
            worker = OutboxWorker(
                PostgresOutboxStore(TEST_DATABASE_URL),
                {"publish_digest": handler},
                batch_size=3,
                clock=self.clock,
            )
            barrier.wait()
            while worker.run_once().delivered:
                pass

        threads = [threading.Thread(target=drain) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(sorted(handler.delivered), sorted(keys))
        for key in keys:
            self.assertEqual(self.store.get(key).status, RecordStatus.DELIVERED)
            self.assertEqual(self.store.get(key).attempts, 1)

    def test_concurrent_submits_of_one_key_create_one_row(self):
        barrier = threading.Barrier(8)

        def submit():
            barrier.wait()
            PostgresOutboxStore(TEST_DATABASE_URL).enqueue(effect(), self.clock())

        threads = [threading.Thread(target=submit) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        with psycopg.connect(TEST_DATABASE_URL) as conn:
            count = conn.execute("SELECT count(*) FROM outbox").fetchone()[0]
        self.assertEqual(count, 1)

    def test_state_survives_a_new_store_instance(self):
        self.store.enqueue(effect(), self.clock())
        self.store.claim(self.clock(), 10, 30.0)
        self.store.release("publish:run-1", "boom", self.clock() + timedelta(seconds=5))

        fresh = PostgresOutboxStore(TEST_DATABASE_URL).get("publish:run-1")

        self.assertEqual(fresh.status, RecordStatus.QUEUED)
        self.assertEqual(fresh.attempts, 1)
        self.assertEqual(fresh.last_error, "boom")


if __name__ == "__main__":
    unittest.main()
