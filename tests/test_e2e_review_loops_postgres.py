from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

import psycopg

from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import RoutingAction
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.persistence.encryption import KEYS_ENV
from course_discovery.privacy import IncompatibleThreadError, ThreadAccessError, authorize_thread, register_thread
from course_discovery.research_agent.search.tavily_client import TavilyClient
from course_discovery.workflows.outer_graph import build_graph
from tests.research_view import research_values
from tests.test_encryption import make_key, spec

USER = "cli-user"
QUERY = "Find free Python courses with certificate for beginners"
RESULTS_PER_QUERY = 5


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class ReviewLoopTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.thread_id = f"e2e-{uuid.uuid4()}"
        env = patch.dict(os.environ, {"DATABASE_URL": self.url, KEYS_ENV: spec(("k1", make_key()))})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        self.published: list[dict] = []
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": self.published.append})))
        self.searched: list[str] = []
        original = TavilyClient.search

        async def counting(client, query, *args, **kwargs):
            self.searched.append(query)
            return await original(client, query, *args, **kwargs)

        patcher = patch.object(TavilyClient, "search", counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.config = {
            "configurable": {"thread_id": self.thread_id, "max_review_rounds": 5},
            "recursion_limit": 60,
        }
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (self.thread_id,))
            conn.execute("DELETE FROM run_threads WHERE thread_id = %s", (self.thread_id,))
            conn.execute("DELETE FROM pending_courses WHERE run_id = %s", (self.thread_id,))

    def _pending(self) -> int:
        with psycopg.connect(self.url) as conn:
            return conn.execute(
                "SELECT count(*) FROM pending_courses WHERE run_id = %s", (self.thread_id,)
            ).fetchone()[0]

    async def _segment(self, first: bool = False, feedback: str | None = None) -> dict:
        """One CLI segment: fresh saver and graph, as after a process restart."""
        async with open_checkpointer(self.url) as saver:
            graph = build_graph(checkpointer=saver)
            visited: list[str] = []
            if first:
                register_thread(USER, self.thread_id)
                inputs = _initial_state(QUERY)
            else:
                authorize_thread(USER, self.thread_id)
                await graph.aupdate_state(self.config, {"manager_feedback": feedback})
                register_thread(USER, self.thread_id)
                inputs = None
            async for namespace, chunk in graph.astream(
                inputs, self.config, stream_mode="updates", subgraphs=True
            ):
                visited.extend(name for name in chunk if not name.startswith("__"))
            snapshot = await graph.aget_state(self.config)
            return {
                "visited": visited,
                "next": snapshot.next,
                "values": snapshot.values,
                "research": await research_values(graph, self.thread_id),
            }

    async def test_four_review_rounds_then_approval_across_restarts(self):
        first = await self._segment(first=True)
        self.assertEqual(first["next"], ("await_human_review",))
        self.assertEqual(first["values"]["research_pass"], 0)
        self.assertEqual(first["research"]["completed_queries"], self.searched)
        self.assertGreater(self._pending(), 0)
        self.assertEqual(first["values"]["metrics"].tavily_calls, len(self.searched))

        rounds = [
            ("augment: more courses", RoutingAction.AUGMENT),
            ("rewrite: make the digest shorter", RoutingAction.REWRITE),
            ("reset: free JavaScript courses for beginners", RoutingAction.RESET),
        ]
        for index, (feedback, action) in enumerate(rounds, start=1):
            searched_before = len(self.searched)
            result = await self._segment(feedback=feedback)
            values = result["values"]

            self.assertEqual(result["next"], ("await_human_review",), feedback)
            self.assertEqual(len(values["feedback_history"]), index, feedback)
            self.assertEqual(values["research_pass"], index, feedback)
            self.assertEqual(values["research_retries"], 0, feedback)
            self.assertNotIn("retry_research_pass", result["visited"], feedback)
            self.assertEqual(values["research_pass"], result["research"]["research_pass"])
            self.assertIsNone(values.get("manager_feedback"))
            self.assertFalse(values.get("discard_reason"), feedback)
            self.assertTrue(values.get("digest"), feedback)
            self.assertIn("begin_pass", result["visited"], feedback)
            self.assertEqual(result["visited"].count("start_research_pass"), 1, feedback)

            queries = result["research"]["completed_queries"]
            self.assertEqual(len(queries), len(set(queries)), f"{feedback}: duplicated queries")
            if action == RoutingAction.RESET:
                self.assertEqual(queries, self.searched[searched_before:], "reset must start from empty state")
                self.assertEqual(len(result["research"]["tavily_results"]), len(queries) * RESULTS_PER_QUERY)
                self.assertEqual(result["research"]["research_iteration"], 0)

            self.assertEqual(values["metrics"].tavily_calls, len(queries), f"{feedback}: metrics follow the ledger")
            self.assertGreater(self._pending(), 0)

        done = await self._segment(feedback="approve")
        self.assertEqual(done["next"], ())
        self.assertIsNotNone(done["values"].get("publish_status"))
        self.assertEqual(len(done["values"]["feedback_history"]), len(rounds) + 1)
        self.assertEqual(len(self.published), 1)

    async def test_discard_after_a_loop_clears_the_staging_and_ends(self):
        await self._segment(first=True)
        await self._segment(feedback="augment: more courses")
        self.assertGreater(self._pending(), 0)

        done = await self._segment(feedback="discard")

        self.assertEqual(done["next"], ())
        self.assertIn("discard_run", done["visited"])
        self.assertIsNone(done["values"].get("publish_status"))
        self.assertEqual(self._pending(), 0)
        self.assertEqual(self.published, [])

    async def test_another_user_cannot_resume_mid_loop(self):
        await self._segment(first=True)
        with self.assertRaises(ThreadAccessError):
            authorize_thread("someone-else", self.thread_id)

    async def test_a_thread_from_another_schema_version_is_refused_mid_loop(self):
        await self._segment(first=True)
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("UPDATE run_threads SET schema_version = 1 WHERE thread_id = %s", (self.thread_id,))
        with self.assertRaises(IncompatibleThreadError):
            await self._segment(feedback="approve")


if __name__ == "__main__":
    unittest.main()
