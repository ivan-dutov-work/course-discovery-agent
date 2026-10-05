from __future__ import annotations

import os
import threading
import unittest
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import psycopg
from outbox_contract import OutboxContract, Recorder, effect

from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import DeliveryStatus
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import OutboxGateway
from course_discovery.effects.models import RecordStatus
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import prune_checkpoints
from course_discovery.privacy import register_thread
from course_discovery.workflows.outer_graph import build_graph
from test_integration_postgres import QUERY, _durable_saver, _thread

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


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class PublishThroughPostgresOutboxTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(MIGRATION.read_text())
            conn.execute("TRUNCATE outbox")
            conn.execute(
                "TRUNCATE recommendation_events, course_evidence, courses, pending_courses, users CASCADE"
            )
            conn.execute("INSERT INTO users (id) VALUES ('cli-user')")
        env = patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.store = PostgresOutboxStore(TEST_DATABASE_URL)
        set_gateway(OutboxGateway(self.store))
        self.addCleanup(set_gateway, None)
        self.handler = Recorder()

    def worker(self) -> OutboxWorker:
        return OutboxWorker(
            PostgresOutboxStore(TEST_DATABASE_URL), {"publish_digest": self.handler}
        )

    def outbox_rows(self) -> int:
        with psycopg.connect(TEST_DATABASE_URL) as conn:
            return conn.execute("SELECT count(*) FROM outbox").fetchone()[0]

    async def test_durable_run_publishes_once_across_restart_and_replay(self):
        thread_id = f"run-outbox-{uuid.uuid4().hex[:8]}"
        config = _thread(thread_id)
        async with _durable_saver() as saver:
            await saver.setup()
            first = build_graph(checkpointer=saver)
            await first.ainvoke(_initial_state(QUERY), config)
            await first.aupdate_state(config, {"manager_feedback": "approve"})
            result = await first.ainvoke(None, config)

        self.assertEqual(result["publish_status"], DeliveryStatus.QUEUED)
        self.assertEqual(self.handler.delivered, [])
        self.assertEqual(self.outbox_rows(), 1)

        self.assertEqual(self.worker().run_once().delivered, 1)

        async with _durable_saver() as saver:
            second = build_graph(checkpointer=saver)
            points = [
                snap
                async for snap in second.aget_state_history(config)
                if snap.next == ("send_approved_courses",)
            ]
            self.assertEqual(len(points), 1)
            replayed = await second.ainvoke(None, points[0].config)

        self.assertEqual(replayed["publish_status"], DeliveryStatus.DELIVERED)
        self.assertEqual(self.outbox_rows(), 1)
        self.assertEqual(self.worker().run_once().delivered, 0)
        self.assertEqual(self.handler.delivered, [f"publish:{thread_id}"])


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class CheckpointPruneTests(unittest.IsolatedAsyncioTestCase):
    async def test_prunes_only_threads_older_than_cutoff(self):
        from datetime import datetime, timedelta, timezone

        env = patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        set_gateway(OutboxGateway(PostgresOutboxStore(TEST_DATABASE_URL)))
        self.addCleanup(set_gateway, None)
        prefix = uuid.uuid4().hex
        old, fresh = f"{prefix}-old", f"{prefix}-fresh"

        async with _durable_saver() as saver:
            await saver.setup()
            graph = build_graph(checkpointer=saver)
            for thread in (old, fresh):
                register_thread(f"user-{thread}", thread)
                await graph.ainvoke(_initial_state(QUERY), _thread(thread))

            future = datetime.now(timezone.utc) + timedelta(days=60)
            pruned = await prune_checkpoints(saver, 30, now=future)
            self.assertIn(old, pruned)
            self.assertIn(fresh, pruned)

            register_thread(f"user-{prefix}-c", f"{prefix}-c")
            await graph.ainvoke(_initial_state(QUERY), _thread(f"{prefix}-c"))
            pruned_now = await prune_checkpoints(saver, 30)
            self.assertNotIn(f"{prefix}-c", pruned_now)
            remaining = [c async for c in saver.alist(_thread(f"{prefix}-c"))]
            self.assertTrue(remaining)
            gone = [c async for c in saver.alist(_thread(old))]
            self.assertEqual(gone, [])
