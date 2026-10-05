from __future__ import annotations

import base64
import json
import os
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg

from course_discovery.domain.contract import STATE_SCHEMA_VERSION, UNVERSIONED
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.persistence.encryption import KEYS_ENV
from course_discovery.privacy import IncompatibleThreadError, authorize_thread
from course_discovery.workflows.outer_graph import build_graph

FIXTURES = Path(__file__).parent / "fixtures" / "checkpoints"
USER_ID = "cli-user"
TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes")
JSONB_COLUMNS = {"checkpoint", "metadata"}


def manifest() -> list[dict]:
    return json.loads((FIXTURES / "manifest.json").read_text())


def _decode(column, value):
    if isinstance(value, dict) and "b64" in value:
        return base64.b64decode(value["b64"])
    if column in JSONB_COLUMNS:
        return json.dumps(value)
    return value


class ManifestTests(unittest.TestCase):
    def test_every_manifest_file_exists(self):
        for entry in manifest():
            self.assertTrue((FIXTURES / entry["file"]).exists(), entry["file"])

    def test_the_current_version_has_a_fixture_that_resumes(self):
        current = [e for e in manifest() if e["version"] == STATE_SCHEMA_VERSION]
        self.assertEqual([e["expect"] for e in current], ["resumes"])

    def test_every_older_version_has_a_fixture_that_is_refused(self):
        older = {e["version"]: e["expect"] for e in manifest() if e["version"] < STATE_SCHEMA_VERSION}
        self.assertEqual(set(older), set(range(UNVERSIONED, STATE_SCHEMA_VERSION)))
        self.assertEqual(set(older.values()), {"refused"})


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class CheckpointCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop(KEYS_ENV, None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))

    def _load(self, entry: dict, thread_id: str) -> None:
        dump = json.loads((FIXTURES / entry["file"]).read_text())
        stored_version = None if entry["version"] == UNVERSIONED else entry["version"]
        with psycopg.connect(self.url) as conn:
            for table in TABLES:
                for row in dump[table]:
                    row = {**row, "thread_id": thread_id}
                    columns = list(row)
                    conn.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) "
                        f"VALUES ({', '.join(['%s'] * len(columns))})",
                        [_decode(c, row[c]) for c in columns],
                    )
            conn.execute(
                "INSERT INTO run_threads (thread_id, user_id, schema_version) VALUES (%s, %s, %s)",
                (thread_id, USER_ID, stored_version),
            )

    def _rows(self, thread_id: str) -> int:
        with psycopg.connect(self.url) as conn:
            return sum(
                conn.execute(f"SELECT count(*) FROM {t} WHERE thread_id = %s", (thread_id,)).fetchone()[0]
                for t in TABLES
            )

    async def _check(self, entry: dict) -> None:
        thread_id = f"fixture-{entry['version']}-{uuid.uuid4()}"
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}
        self._load(entry, thread_id)
        async with open_checkpointer(self.url) as saver:
            graph = build_graph(checkpointer=saver)
            try:
                before = self._rows(thread_id)
                self.assertEqual((await graph.aget_state(config)).next, ("await_human_review",))
                if entry["expect"] == "refused":
                    with self.assertRaises(IncompatibleThreadError) as caught:
                        authorize_thread(USER_ID, thread_id)
                    self.assertEqual(caught.exception.stored, entry["version"])
                    self.assertEqual(caught.exception.current, STATE_SCHEMA_VERSION)
                    self.assertEqual(self._rows(thread_id), before)
                else:
                    authorize_thread(USER_ID, thread_id)
                    await graph.aupdate_state(config, {"manager_feedback": "approve"})
                    result = await graph.ainvoke(None, config)
                    self.assertIsNotNone(result.get("publish_status"))
                    self.assertEqual((await graph.aget_state(config)).next, ())
            finally:
                await saver.adelete_thread(thread_id)
                with psycopg.connect(self.url, autocommit=True) as conn:
                    conn.execute("DELETE FROM run_threads WHERE thread_id = %s", (thread_id,))
                    conn.execute("DELETE FROM pending_courses WHERE run_id = %s", (thread_id,))

    async def test_each_fixture_behaves_as_the_manifest_says(self):
        for entry in manifest():
            with self.subTest(file=entry["file"]):
                await self._check(entry)


if __name__ == "__main__":
    unittest.main()
