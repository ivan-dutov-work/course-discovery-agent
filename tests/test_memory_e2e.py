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
from course_discovery.domain.models import DeliveryStatus, MemoryPatch
from course_discovery.research_agent.memory import nodes as memory_nodes
from course_discovery.research_agent.memory.repository import load_user_memory, save_user_memory
from course_discovery.review import router as router_module
from course_discovery.workflows.outer_graph import build_graph
from tests.curator_stubs import FakeProfiles, ScriptedModel, call, install_curator, reply
from tests.memory_cases import (
    CASES,
    curator,
    OTHER_USER_AVOIDS_UDEMY,
    QUERY,
    MemoryCase,
    StubRouter,
)

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


def providers_of(state: dict) -> set[str]:
    return {course.provider.lower() for course in state["valid_courses"]}


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


def install_fake_profiles(test) -> FakeProfiles:
    profiles = FakeProfiles()
    for target, name, replacement in (
        (memory_nodes, "load_user_memory", profiles.load),
        (gateway_module, "load_user_memory", profiles.load),
        (memory_nodes, "record_feedback", profiles.record_feedback),
    ):
        patcher = patch.object(target, name, replacement)
        patcher.start()
        test.addCleanup(patcher.stop)
    return profiles


class MemoryCaseScenario(CourseLengthScenario):
    scripted = True

    def _install_case(self, case: MemoryCase) -> tuple[ScriptedModel | None, list[str]]:
        names: list[str] = []
        script = case.steps

        def recording(messages, turn):
            message = curator(*script)(messages, turn)
            names.extend(c["name"] for c in message.tool_calls)
            return message

        model = ScriptedModel(recording)
        if self.scripted:
            self.use_curator(model)
        if self.scripted and case.router:
            for name, value in (
                ("llm_enabled", lambda: True),
                ("build_llm", lambda *a, **k: StubRouter(case.router)),
            ):
                patcher = patch.object(router_module, name, value)
                patcher.start()
                self.addCleanup(patcher.stop)
        return model, names

    def check_notes(self, case: MemoryCase, before, after, feedback: str) -> None:
        added = after.notes[len(before.notes) :]
        self.assertEqual([n.scope for n in added], [case.note_scope])
        text = added[0].text.lower()
        self.assertFalse([word for word in case.no_note_mentions if word in text], text)

    async def check_case(self, case: MemoryCase) -> None:
        user = self.user("case")
        other = self.user("other")
        self.seed(other, OTHER_USER_AVOIDS_UDEMY)
        if case.stored:
            self.seed(user, case.stored)
        before = self.profile(user)
        baseline = await self._run(self.user("baseline"), ["approve"])
        baseline_urls = digest_urls(baseline["digest"])
        model, trace = self._install_case(case)

        first = await self._run(user, case.feedbacks)
        after = self.profile(user)

        self.assertEqual(first["memory_update"], case.memory_update)
        if self.scripted:
            self.assertEqual(trace, case.trace)
        published = first["publish_status"] in {DeliveryStatus.QUEUED, DeliveryStatus.DELIVERED}
        self.assertEqual(published, case.published)
        for name, value in case.profile.items():
            self.assertEqual(getattr(after, name), value, name)
        if case.unchanged:
            self.assertEqual(after, before)
        if case.no_pii:
            seen = [m.content for messages in model.seen for m in messages] if self.scripted else []
            stored = [*first["feedback_history"], *self.recorded_texts(user), after.model_dump_json(), *seen]
            self.assertTrue(all(case.no_pii not in text for text in stored))
        else:
            self.assertEqual(first["feedback_history"], case.history or case.feedbacks)
        if case.note_scope:
            self.check_notes(case, before, after, first["feedback_history"][0])
        if case.other_user_keeps:
            self.assertEqual(self.profile(other).avoided_providers, list(case.other_user_keeps))

        second = await self._run(user, ["approve"])
        second_urls = digest_urls(second["digest"])
        second_providers = providers_of(second)
        self.assertTrue(second_urls)
        if case.unchanged:
            self.assertEqual(second_urls, baseline_urls)
        for provider in case.drops_providers:
            self.assertIn(provider, providers_of(baseline))
            self.assertNotIn(provider, second_providers)
        if case.drops_first_udemy_url:
            (rejected,) = after.rejected_course_urls
            self.assertIn(rejected, baseline_urls)
            self.assertNotIn(rejected, second_urls)
        if case.keeps_other_udemy:
            self.assertIn(AVOIDED, second_providers)


def _case_test(case: MemoryCase):
    async def test(self):
        await self.check_case(case)

    return test


for _case in (c for c in CASES if not c.live_only):
    setattr(MemoryCaseScenario, f"test_{_case.id}", _case_test(_case))


class FakeStoreE2E(MemoryCaseScenario, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))

        self.profiles = install_fake_profiles(self)
        install_curator(self, ScriptedModel(history_curator), self.profiles)

    def user(self, label: str) -> str:
        return f"{label}-{uuid.uuid4().hex[:6]}"

    def profile(self, user_id):
        return self.profiles.load(user_id)

    def recorded_rejection(self, user_id):
        (call,) = [c for c in self.profiles.feedback_calls if c["user_id"] == user_id]
        return (not call["accepted"], call["feedback_text"])

    def use_curator(self, model):
        install_curator(self, model, self.profiles)

    def seed(self, user_id, patch_: MemoryPatch):
        self.profiles.save(user_id, patch_)

    def recorded_texts(self, user_id):
        return [c["feedback_text"] for c in self.profiles.feedback_calls if c["user_id"] == user_id]


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class PostgresE2E(MemoryCaseScenario, unittest.IsolatedAsyncioTestCase):
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

    def use_curator(self, model):
        install_curator(self, model)

    def seed(self, user_id, patch_: MemoryPatch):
        save_user_memory(user_id, patch_)

    def recorded_texts(self, user_id):
        rows = self.conn.execute(
            "SELECT DISTINCT feedback_text FROM recommendation_events WHERE user_id = %s", (user_id,)
        ).fetchall()
        return [row[0] for row in rows]
