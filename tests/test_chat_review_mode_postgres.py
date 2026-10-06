from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg

from course_discovery.app.cli import _initial_state
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.handlers import PUBLISH_DIGEST, SEND_DIGEST_MESSAGE, SEND_FEEDBACK_PROMPT
from course_discovery.effects.models import Effect
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.privacy import IncompatibleThreadError, ThreadAccessError, register_thread
from course_discovery.review import nodes as review_nodes
from course_discovery.review.close import close_thread
from course_discovery.workflows.outer_graph import build_graph

QUERY = "Find free Python courses with certificate for beginners"


class Crash(Exception):
    pass


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class ChatReviewModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.thread_id = f"chat-{uuid.uuid4()}"
        self.user = f"chat-user-{uuid.uuid4()}"
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.addCleanup(set_gateway, None)
        self.sent: list = []
        self.published: list = []
        handlers = {
            SEND_DIGEST_MESSAGE: self.sent.append,
            SEND_FEEDBACK_PROMPT: self.sent.append,
            PUBLISH_DIGEST: self.published.append,
        }
        store = PostgresOutboxStore(self.url)
        set_gateway(InlineGateway(store, OutboxWorker(store, handlers)))
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (self.thread_id,))
            conn.execute("DELETE FROM run_threads WHERE thread_id = %s", (self.thread_id,))
            conn.execute("DELETE FROM pending_courses WHERE run_id = %s", (self.thread_id,))
            conn.execute("DELETE FROM outbox WHERE payload->>'run_id' = %s", (self.thread_id,))
            conn.execute("DELETE FROM recommendation_events WHERE user_id = %s", (self.user,))
            conn.execute("DELETE FROM users WHERE id = %s", (self.user,))

    def _config(self, **extra) -> dict:
        return {
            "configurable": {"thread_id": self.thread_id, "chat_mode": True, **extra},
            "recursion_limit": 60,
        }

    async def _segment(self, first: bool = False, feedback: str | None = None) -> tuple:
        async with open_checkpointer(self.url) as saver:
            graph = build_graph(checkpointer=saver)
            config = self._config()
            if first:
                register_thread(self.user, self.thread_id)
                inputs = {**_initial_state(QUERY), "user_id": self.user}
            else:
                await graph.aupdate_state(config, {"manager_feedback": feedback})
                inputs = None
            async for _ in graph.astream(inputs, config, stream_mode="updates", durability="sync"):
                pass
            return (await graph.aget_state(config)).next

    async def _close(self, reason: str = "implicit") -> bool:
        async with open_checkpointer(self.url) as saver:
            return await close_thread(build_graph(checkpointer=saver), self.user, self.thread_id, reason=reason)

    def _count(self, sql: str, *params) -> int:
        with psycopg.connect(self.url) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def _digests(self) -> list:
        return [effect for effect in self.sent if effect.kind == SEND_DIGEST_MESSAGE]

    def _prompts(self) -> list:
        return [effect for effect in self.sent if effect.kind == SEND_FEEDBACK_PROMPT]

    def _events(self) -> int:
        return self._count(
            "SELECT count(*) FROM recommendation_events WHERE user_id = %s AND accepted", self.user
        )

    def _pending(self) -> int:
        return self._count("SELECT count(*) FROM pending_courses WHERE run_id = %s", self.thread_id)

    def _catalogue(self) -> int:
        return self._count("SELECT count(*) FROM courses")

    async def test_chat_runs_send_one_digest_per_round_and_close_as_accepted(self):
        catalogue_before = self._catalogue()
        self.assertEqual(await self._segment(first=True), ("await_human_review",))
        self.assertEqual(await self._segment(feedback="rewrite: make the digest shorter"), ("await_human_review",))

        self.assertEqual(
            [effect.key for effect in self._digests()],
            [f"digest:{self.thread_id}:0", f"digest:{self.thread_id}:1"],
        )
        self.assertEqual(len(self._prompts()), 1)
        pending = self._pending()
        self.assertGreater(pending, 0)

        self.assertTrue(await self._close())

        self.assertEqual(self.published, [])
        self.assertEqual(len(self._digests()), 2)
        self.assertGreater(self._events(), 0)
        self.assertEqual(self._catalogue(), catalogue_before)
        self.assertEqual(self._pending(), pending)
        reasons = self._count(
            "SELECT count(*) FROM recommendation_events "
            "WHERE user_id = %s AND recommendation_reason = 'implicit'",
            self.user,
        )
        self.assertEqual(reasons, self._events())

    async def test_a_crash_after_the_digest_effect_does_not_send_it_twice(self):
        real = review_nodes.send_review_digest_node
        crashes = []

        def crash_after_submit(state, config):
            real(state, config)
            if not crashes:
                crashes.append(1)
                raise Crash

        with patch("course_discovery.workflows.outer_graph.send_review_digest_node", crash_after_submit):
            with self.assertRaises(Crash):
                await self._segment(first=True)
        self.assertEqual(len(self._digests()), 1)

        async with open_checkpointer(self.url) as saver:
            graph = build_graph(checkpointer=saver)
            async for _ in graph.astream(None, self._config(), stream_mode="updates", durability="sync"):
                pass
            self.assertEqual((await graph.aget_state(self._config())).next, ("await_human_review",))

        self.assertEqual(len(self._digests()), 1)
        self.assertEqual(len(self._prompts()), 1)

    async def test_closing_twice_and_concurrently_writes_one_set_of_events(self):
        await self._segment(first=True)
        catalogue_before = self._catalogue()

        results = await asyncio.gather(self._close(), self._close())
        self.assertTrue(any(results))
        events = self._events()
        self.assertGreater(events, 0)

        self.assertFalse(await self._close())
        self.assertEqual(self._events(), events)
        self.assertEqual(self._catalogue(), catalogue_before)
        self.assertGreater(self._pending(), 0)

    async def test_closing_is_refused_for_the_wrong_owner(self):
        await self._segment(first=True)
        async with open_checkpointer(self.url) as saver:
            with self.assertRaises(ThreadAccessError):
                await close_thread(build_graph(checkpointer=saver), "someone-else", self.thread_id)
        self.assertEqual(self._events(), 0)

    async def test_closing_is_refused_under_another_schema_version(self):
        await self._segment(first=True)
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("UPDATE run_threads SET schema_version = 1 WHERE thread_id = %s", (self.thread_id,))
        with self.assertRaises(IncompatibleThreadError):
            await self._close()
        self.assertEqual(self._events(), 0)

    async def test_the_cli_mode_sends_no_chat_messages(self):
        async with open_checkpointer(self.url) as saver:
            graph = build_graph(checkpointer=saver)
            config = {"configurable": {"thread_id": self.thread_id}, "recursion_limit": 60}
            register_thread(self.user, self.thread_id)
            state = {**_initial_state(QUERY), "user_id": self.user}
            async for _ in graph.astream(state, config, stream_mode="updates", durability="sync"):
                pass
        self.assertEqual(self.sent, [])


class DigestKeyTests(unittest.TestCase):
    def test_effect_keys_are_derived_from_run_and_pass(self):
        sent: list[Effect] = []

        class Recorder:
            def submit(self, effect):
                sent.append(effect)

        with patch.object(review_nodes, "get_gateway", return_value=Recorder()):
            state = {"user_id": "u", "digest": "d", "valid_courses": [], "research_pass": 2}
            config = {"configurable": {"thread_id": "t-1", "chat_mode": True}}
            review_nodes.send_review_digest_node(state, config)
            review_nodes.send_review_digest_node(state, config)

        self.assertEqual([e.key for e in sent[:2]], ["digest:t-1:2", "feedback_prompt:t-1"])
        self.assertEqual([e.key for e in sent[:2]], [e.key for e in sent[2:]])


if __name__ == "__main__":
    unittest.main()
