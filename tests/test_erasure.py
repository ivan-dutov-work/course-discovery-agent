from __future__ import annotations

import base64
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from langgraph.checkpoint.base import empty_checkpoint

from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.persistence.encryption import KEYS_ENV
from course_discovery.privacy import erase_user, register_thread
from course_discovery.privacy.__main__ import main
from course_discovery.privacy.sources import NOT_USER_DATA, USER_DATA_SOURCES


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class ErasureTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        tag = uuid.uuid4().hex[:8]
        self.user, self.other = f"user-{tag}", f"other-{tag}"
        self.env = patch.dict(
            os.environ,
            {
                "DATABASE_URL": self.url,
                KEYS_ENV: "k1:" + base64.b64encode(os.urandom(32)).decode(),
            },
        )
        self.env.start()
        self.conn = psycopg.connect(self.url, autocommit=True)

    def tearDown(self):
        for user in (self.user, self.other):
            for sql in (
                "DELETE FROM outbox WHERE payload ->> 'user_id' = %s",
                "DELETE FROM recommendation_events WHERE feedback_text = 'feedback-' || %s",
                "DELETE FROM users WHERE id = %s",
                "DELETE FROM run_threads WHERE user_id = %s",
            ):
                self.conn.execute(sql, (user,))
        self.conn.close()
        self.env.stop()

    async def _seed(self, saver, user: str) -> str:
        thread_id = f"t-{uuid.uuid4()}"
        register_thread(user, thread_id)
        checkpoint = empty_checkpoint()
        checkpoint["channel_values"] = {"user_id": user, "digest": f"digest for {user}"}
        checkpoint["channel_versions"] = {"user_id": "1", "digest": "1"}
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        await saver.aput(config, checkpoint, {"source": "input", "step": 0}, checkpoint["channel_versions"])
        await saver.aput_writes(
            {"configurable": {**config["configurable"], "checkpoint_id": checkpoint["id"]}},
            [("digest", f"digest for {user}")],
            "task-1",
        )
        self.conn.execute("INSERT INTO users (id) VALUES (%s)", (user,))
        self.conn.execute(
            "INSERT INTO user_preferences (user_id, career_goals) VALUES (%s, %s)", (user, ["ml"])
        )
        self.conn.execute(
            "INSERT INTO recommendation_events (user_id, query, rank, feedback_text) "
            "VALUES (%s, 'python', 1, %s)",
            (user, f"feedback-{user}"),
        )
        for status in ("queued", "dead"):
            self.conn.execute(
                "INSERT INTO outbox (key, kind, payload, status) VALUES (%s, 'publish_digest', %s::jsonb, %s)",
                (f"k-{uuid.uuid4()}", f'{{"user_id": "{user}"}}', status),
            )
        return thread_id

    def _count(self, sql: str, *args) -> int:
        return self.conn.execute(sql, args).fetchone()[0]

    async def test_dry_run_reports_without_deleting(self):
        async with open_checkpointer() as saver:
            thread = await self._seed(saver, self.user)
            report = await erase_user(self.user, saver)
        self.assertFalse(report.executed)
        self.assertEqual(report.threads, [thread])
        self.assertEqual(report.outbox_by_status, {"queued": 1, "dead": 1})
        self.assertEqual(report.rows["recommendation_events"], 1)
        self.assertEqual(self._count("SELECT count(*) FROM checkpoints WHERE thread_id = %s", thread), 1)

    async def test_execute_removes_only_the_target_user(self):
        async with open_checkpointer() as saver:
            thread = await self._seed(saver, self.user)
            other_thread = await self._seed(saver, self.other)
            report = await erase_user(self.user, saver, execute=True)

        self.assertTrue(report.clean, report.residual)
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            self.assertEqual(self._count(f"SELECT count(*) FROM {table} WHERE thread_id = %s", thread), 0)
        self.assertEqual(self._count("SELECT count(*) FROM user_preferences WHERE user_id = %s", self.user), 0)
        self.assertEqual(
            self._count("SELECT count(*) FROM recommendation_events WHERE feedback_text = %s", f"feedback-{self.user}"), 0
        )
        self.assertEqual(self._count("SELECT count(*) FROM outbox WHERE payload ->> 'user_id' = %s", self.user), 0)

        self.assertEqual(self._count("SELECT count(*) FROM checkpoints WHERE thread_id = %s", other_thread), 1)
        self.assertEqual(self._count("SELECT count(*) FROM user_preferences WHERE user_id = %s", self.other), 1)
        self.assertEqual(self._count("SELECT count(*) FROM outbox WHERE payload ->> 'user_id' = %s", self.other), 2)
        self.assertEqual(self._count("SELECT count(*) FROM run_threads WHERE user_id = %s", self.other), 1)

    async def test_erasing_twice_is_a_noop(self):
        async with open_checkpointer() as saver:
            await self._seed(saver, self.user)
            await erase_user(self.user, saver, execute=True)
            again = await erase_user(self.user, saver, execute=True)
        self.assertEqual(again.threads, [])
        self.assertTrue(again.clean)

    async def test_erasure_works_without_the_encryption_key(self):
        async with open_checkpointer() as saver:
            thread = await self._seed(saver, self.user)
        with patch.dict(os.environ, {KEYS_ENV: "k2:" + base64.b64encode(os.urandom(32)).decode()}):
            async with open_checkpointer() as saver:
                report = await erase_user(self.user, saver, execute=True)
        self.assertTrue(report.clean, report.residual)
        self.assertEqual(self._count("SELECT count(*) FROM checkpoints WHERE thread_id = %s", thread), 0)

    async def test_survivors_that_cannot_be_read_are_reported_not_ignored(self):
        async with open_checkpointer() as saver:
            await self._seed(saver, self.user)
            with patch.object(saver.inner, "adelete_thread", return_value=None):
                report = await erase_user(self.user, saver, execute=True)
        self.assertFalse(report.clean)
        self.assertEqual(report.residual["checkpoint_threads"], 1)
        async with open_checkpointer() as saver:
            await erase_user(self.user, saver, execute=True)

    async def test_unknown_user_is_clean(self):
        async with open_checkpointer() as saver:
            report = await erase_user(f"nobody-{uuid.uuid4()}", saver, execute=True)
        self.assertEqual(report.threads, [])
        self.assertTrue(report.clean)

    async def test_every_table_is_either_erased_or_declared_not_user_data(self):
        async with open_checkpointer():
            pass
        tables = {
            row[0]
            for row in self.conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        covered = {source.table for source in USER_DATA_SOURCES} | NOT_USER_DATA
        self.assertEqual(tables - covered, set(), "new table: add it to privacy/sources.py")

    def test_cli_defaults_to_dry_run(self):
        with patch("builtins.print"):
            self.assertEqual(main(["erase", "--user-id", f"nobody-{uuid.uuid4()}"]), 0)


if __name__ == "__main__":
    unittest.main()
