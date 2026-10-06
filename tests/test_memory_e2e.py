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
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.research_agent.memory import nodes as memory_nodes
from course_discovery.research_agent.memory.repository import load_user_memory
from course_discovery.workflows.outer_graph import build_graph
from tests.curator_stubs import FakeProfiles, ScriptedModel, call, install_curator, reply

QUERY = "python courses"
AVOIDED = "udemy"


def history_curator(messages, _turn):
    calls = sum(1 for m in messages if m.type == "tool")
    if calls == 0:
        return reply(call("read_profile"))
    text = messages[1].content
    prefer = [p.lower() for p in re.findall(r"prefer (\w+)", text, re.I)]
    avoid = [p.lower() for p in re.findall(r"done with (\w+)", text, re.I)]
    if calls == 1 and "too long" in text:
        patch_ = {"set": {"preferred_course_length": "short"}}
        return reply(call("propose_patch", scope="durable", reason="stated", patch=patch_))
    if calls == 1 and (prefer or avoid):
        patch_ = {"add": {"preferred_providers": prefer, "avoided_providers": avoid}}
        return reply(call("propose_patch", scope="durable", reason="stated", patch=patch_))
    return reply(call("finish", reason="done"))


def digest_urls(digest: str) -> list[str]:
    return re.findall(r"- URL: (\S+)", digest)


def provider_of(url: str) -> str:
    host = re.sub(r"^https?://(www\.)?", "", url).split("/")[0]
    return host.split(".")[0]


class FeedbackToNextRunScenario:
    """Run one collects review feedback; run two must behave differently because of it."""

    async def _run(self, user_id: str, feedbacks: list[str]) -> dict:
        thread_id = f"e2e-{uuid.uuid4()}"
        graph = build_graph()
        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        await graph.ainvoke({**_initial_state(QUERY), "user_id": user_id}, config)
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
        self.assertIsNone(first["manager_feedback"])
        self.assertIsNone(first["publish_status"])
        self.assertEqual(self.recorded_rejection(user), (True, f"{round_one}\n{round_two}"))

        self.assertEqual(first["memory_update"], "committed")
        stored = self.profile(user)
        self.assertEqual(stored.preferred_providers, [preferred])
        self.assertEqual(stored.avoided_providers, [AVOIDED])

        second = await self._run(user, ["approve"])
        second_urls = digest_urls(second["digest"])
        second_providers = [provider_of(url) for url in second_urls]

        self.assertEqual(second["feedback_history"], ["approve"])
        self.assertEqual(second["memory_update"], "skipped:no_feedback")
        self.assertNotIn(AVOIDED, second_providers)
        self.assertEqual(second_providers[0], preferred)
        self.assertEqual({provider_of(c.url) for c in second["valid_courses"]} & {AVOIDED}, set())


class CourseLengthScenario(FeedbackToNextRunScenario):
    async def test_too_long_feedback_is_stored_and_run_two_leads_with_a_short_course(self):
        user = self.user("busy")
        feedback = "discard: too long, I have 2 hours a week"
        first = await self._run(user, [feedback])
        self.assertEqual(first["memory_update"], "committed")
        self.assertEqual(self.profile(user).preferred_course_length, "short")

        second = await self._run(user, ["approve"])
        first_course = {c.url: c for c in second["valid_courses"]}[digest_urls(second["digest"])[0]]
        self.assertLessEqual(first_course.duration_hours, 10)


class FakeStoreE2E(CourseLengthScenario, unittest.IsolatedAsyncioTestCase):
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
        install_curator(self, ScriptedModel(history_curator), self.profiles)
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

    def profile(self, user_id):
        return self.profiles.load(user_id)

    def recorded_rejection(self, user_id):
        (call,) = [c for c in self.profiles.feedback_calls if c["user_id"] == user_id]
        return (not call["accepted"], call["feedback_text"])



@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class PostgresE2E(CourseLengthScenario, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        self.addCleanup(set_gateway, None)
        install_curator(self, ScriptedModel(history_curator))
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))
        self.conn = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.conn.close)
        self.users: list[str] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for user in self.users:
            self.conn.execute("DELETE FROM memory_updates WHERE user_id = %s", (user,))
            self.conn.execute("DELETE FROM recommendation_events WHERE user_id = %s", (user,))
            self.conn.execute("DELETE FROM users WHERE id = %s", (user,))

    def user(self, label: str) -> str:
        user = f"{label}-{uuid.uuid4().hex[:6]}"
        self.users.append(user)
        return user

    def profile(self, user_id):
        return load_user_memory(user_id)

    def recorded_rejection(self, user_id):
        rows = self.conn.execute(
            "SELECT DISTINCT rejected, feedback_text FROM recommendation_events WHERE user_id = %s",
            (user_id,),
        ).fetchall()
        self.assertEqual(len(rows), 1)
        return rows[0]
