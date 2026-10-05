from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from psycopg.rows import dict_row

from course_discovery.app.cli import _initial_state
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.persistence.encryption import KEYS_ENV
from course_discovery.workflows.outer_graph import build_graph
from tests.test_encryption import make_key, spec

CANARY = "zebra7431"


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class NestedCheckpointTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.thread_id = f"nested-{uuid.uuid4()}"
        env = patch.dict(os.environ, {KEYS_ENV: spec(("k1", make_key()))})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))

    def _rows(self, sql: str, *args) -> list[dict]:
        with psycopg.connect(self.url, row_factory=dict_row) as conn:
            return conn.execute(sql, (self.thread_id, *args)).fetchall()

    async def test_research_subgraph_checkpoints_are_sealed_and_erasable(self):
        config = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": 60}
        async with open_checkpointer(self.url) as saver:
            graph = build_graph(checkpointer=saver)
            try:
                async for _ in graph.astream(
                    _initial_state(f"Find free Python courses {CANARY}"), config
                ):
                    pass

                namespaces = {
                    row["checkpoint_ns"]
                    for row in self._rows("SELECT DISTINCT checkpoint_ns FROM checkpoints WHERE thread_id = %s")
                }
                self.assertIn("course_research", namespaces)
                for table, predicate in (
                    ("checkpoints", "strpos(checkpoint::text, %s) > 0"),
                    ("checkpoint_blobs", "position(convert_to(%s, 'UTF8') in blob) > 0"),
                    ("checkpoint_writes", "position(convert_to(%s, 'UTF8') in blob) > 0"),
                ):
                    found = self._rows(
                        f"SELECT count(*) AS n FROM {table} WHERE thread_id = %s AND {predicate}", CANARY
                    )
                    self.assertEqual(found[0]["n"], 0, table)
            finally:
                await saver.adelete_thread(self.thread_id)

        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            self.assertEqual(
                self._rows(f"SELECT count(*) AS n FROM {table} WHERE thread_id = %s")[0]["n"], 0, table
            )


if __name__ == "__main__":
    unittest.main()
