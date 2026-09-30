from __future__ import annotations

import os
import re
import unittest
import uuid
from unittest.mock import patch

import psycopg
from langchain_core.runnables import RunnableConfig

from course_discovery.app import gateway as gateway_module
from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import MemoryPatch, UserMemory
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.research_agent.memory import nodes as memory_nodes
from course_discovery.research_agent.memory.repository import (
    apply_patch,
    load_user_memory,
    save_user_memory,
)
from course_discovery.workflows.outer_graph import build_graph

QUERY = "python courses"
AVOIDED = "udemy"


def curate(feedback_history: list[str]) -> MemoryPatch:
    """Test-local stand-in for the memory curator: reads only `feedback_history`."""
    prefer: list[str] = []
    avoid: list[str] = []
    for line in feedback_history:
        prefer += re.findall(r"prefer (\w+)", line, re.I)
        avoid += re.findall(r"done with (\w+)", line, re.I)
    return MemoryPatch(
        add={
            "preferred_providers": [p.lower() for p in prefer],
            "avoided_providers": [p.lower() for p in avoid],
        }
    )


def digest_urls(digest: str) -> list[str]:
    return re.findall(r"- URL: (\S+)", digest)


def provider_of(url: str) -> str:
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return host.split(".")[0]


class FakeProfiles:
    def __init__(self):
        self.memories: dict[str, UserMemory] = {}
        self.feedback_calls: list[dict] = []

    def load(self, user_id):
        return self.memories.get(user_id, UserMemory())

    def save(self, user_id, patch):
        self.memories[user_id] = apply_patch(self.load(user_id), patch)

    def record_feedback(self, user_id, courses, query, **kwargs):
        self.feedback_calls.append({"user_id": user_id, "count": len(courses), **kwargs})


class FeedbackToNextRunScenario:
    """Run one collects review feedback; run two must behave differently because of it."""

    async def _run(self, user_id: str, feedbacks: list[str]) -> dict:
        thread_id = f"e2e-{uuid.uuid4()}"
        graph = build_graph()
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        await graph.ainvoke({**_initial_state(QUERY, thread_id), "user_id": user_id}, config)
        for feedback in feedbacks:
            graph.update_state(config, {"manager_feedback": feedback})
            await graph.ainvoke(None, config)
        return (await graph.aget_state(config)).values

    async def test_feedback_history_changes_the_next_run(self):
        baseline = await self._run(self.user("baseline"), ["approve"])
        order = digest_urls(baseline["digest"])
        providers = [provider_of(url) for url in order]
        self.assertIn(AVOIDED, providers)
        candidates = [p for p in dict.fromkeys(providers) if p != AVOIDED]
        self.assertGreaterEqual(len(candidates), 2)
        preferred = candidates[-1]
        self.assertNotEqual(providers[0], preferred)

        user = self.user("learner")
        round_one = f"rewrite: I prefer {preferred} courses"
        round_two = f"discard: I'm done with {AVOIDED}"
        first = await self._run(user, [round_one, round_two])

        self.assertEqual(first["feedback_history"], [round_one, round_two])
        self.assertEqual(first["manager_feedback"], round_two)
        self.assertIsNone(first["publish_status"])
        self.assertEqual(self.recorded_rejection(user), (True, f"{round_one}\n{round_two}"))

        patch_from_history = curate(first["feedback_history"])
        self.assertEqual(patch_from_history.add["preferred_providers"], [preferred])
        self.assertEqual(patch_from_history.add["avoided_providers"], [AVOIDED])
        self.assertEqual(curate([first["manager_feedback"]]).add["preferred_providers"], [])
        self.save(user, patch_from_history)

        second = await self._run(user, ["approve"])
        second_urls = digest_urls(second["digest"])
        second_providers = [provider_of(url) for url in second_urls]

        self.assertEqual(second["feedback_history"], ["approve"])
        self.assertNotIn(AVOIDED, second_providers)
        self.assertEqual(second_providers[0], preferred)
        self.assertEqual({provider_of(c.url) for c in second["valid_courses"]} & {AVOIDED}, set())


class FakeStoreE2E(FeedbackToNextRunScenario, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))

        self.profiles = FakeProfiles()
        for target, name, replacement in (
            (memory_nodes, "load_user_memory", self.profiles.load),
            (gateway_module, "load_user_memory", self.profiles.load),
            (memory_nodes, "record_feedback", self.profiles.record_feedback),
        ):
            patcher = patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)

    def user(self, label: str) -> str:
        return f"{label}-{uuid.uuid4().hex[:6]}"

    def save(self, user_id, patch_):
        self.profiles.save(user_id, patch_)

    def recorded_rejection(self, user_id):
        (call,) = [c for c in self.profiles.feedback_calls if c["user_id"] == user_id]
        return (not call["accepted"], call["feedback_text"])

    recorded_rejection_note = "the fake records the call the node makes; Postgres records the row"


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class PostgresE2E(FeedbackToNextRunScenario, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))
        self.conn = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.conn.close)
        self.users: list[str] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for user in self.users:
            self.conn.execute("DELETE FROM recommendation_events WHERE user_id = %s", (user,))
            self.conn.execute("DELETE FROM users WHERE id = %s", (user,))

    def user(self, label: str) -> str:
        user = f"{label}-{uuid.uuid4().hex[:6]}"
        self.users.append(user)
        return user

    def save(self, user_id, patch_):
        save_user_memory(user_id, patch_)
        self.assertEqual(load_user_memory(user_id).avoided_providers, patch_.add["avoided_providers"])

    def recorded_rejection(self, user_id):
        rows = self.conn.execute(
            "SELECT DISTINCT rejected, feedback_text FROM recommendation_events WHERE user_id = %s",
            (user_id,),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        return rows[0]
