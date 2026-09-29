from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from langchain_core.runnables import RunnableConfig

from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import DeliveryStatus
from course_discovery.effects.factory import build_gateway, set_gateway
from course_discovery.effects.gateway import InlineGateway, OutboxGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.models import Effect
from course_discovery.effects.worker import OutboxWorker
from course_discovery.review.router import discard_node, publish_node
from course_discovery.workflows.outer_graph import build_graph

QUERY = "Find free Python courses with certificate for beginners"


class Delivered:
    def __init__(self, *failures: Exception) -> None:
        self.failures = list(failures)
        self.effects: list[Effect] = []

    def __call__(self, effect: Effect) -> None:
        if self.failures:
            raise self.failures.pop(0)
        self.effects.append(effect)


def _config(run_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": run_id}}


async def _run_to_publish(graph, run_id: str) -> dict:
    await graph.ainvoke(_initial_state(QUERY, run_id), _config(run_id))
    graph.update_state(_config(run_id), {"manager_feedback": "approve"})
    return await graph.ainvoke(None, _config(run_id))


class PublishEffectTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        self.store = InMemoryOutboxStore()
        self.handler = Delivered()
        self.worker = OutboxWorker(
            self.store, {"publish_digest": self.handler}, max_attempts=3, backoff=lambda _: 0.0
        )

    async def test_inline_gateway_publishes_and_reports_delivered(self):
        set_gateway(InlineGateway(self.store, self.worker))

        with self.assertNoLogs("langgraph.checkpoint.serde.jsonplus", "WARNING"):
            result = await _run_to_publish(build_graph(), "run-inline")

        self.assertEqual(result["publish_status"], DeliveryStatus.DELIVERED)
        self.assertEqual([e.key for e in self.handler.effects], ["publish:run-inline"])
        payload = self.handler.effects[0].payload
        self.assertEqual(payload["digest"], result["digest"])
        self.assertEqual(payload["run_id"], "run-inline")
        self.assertEqual(
            payload["course_urls"], [course.url for course in result["valid_courses"]]
        )

    async def test_outbox_gateway_reports_queued_until_the_worker_runs(self):
        set_gateway(OutboxGateway(self.store))

        result = await _run_to_publish(build_graph(), "run-queued")

        self.assertEqual(result["publish_status"], DeliveryStatus.QUEUED)
        self.assertEqual(self.handler.effects, [])
        self.assertEqual(self.worker.run_once().delivered, 1)
        self.assertEqual(len(self.handler.effects), 1)

    async def test_replaying_publish_node_does_not_deliver_twice(self):
        set_gateway(OutboxGateway(self.store))
        graph = build_graph()
        await _run_to_publish(graph, "run-replay")
        self.worker.run_once()

        replay_points = [
            snap for snap in graph.get_state_history(_config("run-replay"))
            if snap.next == ("publish_node",)
        ]
        self.assertEqual(len(replay_points), 1)
        result = await graph.ainvoke(None, replay_points[0].config)
        self.worker.run_once()

        self.assertEqual(len(self.handler.effects), 1)
        self.assertEqual(result["publish_status"], DeliveryStatus.DELIVERED)

    async def test_failed_inline_delivery_is_queued_and_finished_by_the_worker(self):
        self.handler.failures.append(ConnectionError("down"))
        set_gateway(InlineGateway(self.store, self.worker))

        result = await _run_to_publish(build_graph(), "run-flaky")

        self.assertEqual(result["publish_status"], DeliveryStatus.QUEUED)
        self.assertEqual(self.worker.run_once().delivered, 1)
        self.assertEqual(len(self.handler.effects), 1)

    async def test_submit_failure_raises_and_leaves_the_run_resumable(self):
        class Unavailable:
            calls = 0

            def submit(self, effect):
                Unavailable.calls += 1
                raise ConnectionError("outbox down")

        set_gateway(Unavailable())
        graph = build_graph()
        await graph.ainvoke(_initial_state(QUERY, "run-down"), _config("run-down"))
        graph.update_state(_config("run-down"), {"manager_feedback": "approve"})

        with self.assertRaises(ConnectionError):
            await graph.ainvoke(None, _config("run-down"))
        self.assertEqual(graph.get_state(_config("run-down")).next, ("publish_node",))

        set_gateway(OutboxGateway(self.store))
        result = await graph.ainvoke(None, _config("run-down"))
        self.assertEqual(result["publish_status"], DeliveryStatus.QUEUED)

    async def test_discard_submits_nothing(self):
        set_gateway(OutboxGateway(self.store))
        graph = build_graph()
        await graph.ainvoke(_initial_state(QUERY, "run-discard"), _config("run-discard"))
        graph.update_state(_config("run-discard"), {"manager_feedback": "discard"})

        result = await graph.ainvoke(None, _config("run-discard"))

        self.assertIsNone(result["publish_status"])
        self.assertIsNone(self.store.get("publish:run-discard"))


class PublishNodeTests(unittest.TestCase):
    def tearDown(self):
        set_gateway(None)

    def test_key_is_derived_from_run_id_and_stable_across_calls(self):
        store = InMemoryOutboxStore()
        set_gateway(OutboxGateway(store))
        state = {"run_id": "r1", "digest": "d", "valid_courses": [], "user_query": "q"}

        first = publish_node(state)
        second = publish_node(state)

        self.assertEqual(first, second)
        self.assertEqual(store.get("publish:r1").effect.payload["digest"], "d")

    def test_discard_node_clears_publish_status(self):
        self.assertEqual(discard_node({"run_id": "r1"}), {"publish_status": None})


class FactoryTests(unittest.TestCase):
    def test_default_is_inline(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(build_gateway(), InlineGateway)

    def test_outbox_mode_requires_database_url(self):
        with patch.dict(os.environ, {"EFFECT_GATEWAY": "outbox"}, clear=True):
            with self.assertRaises(RuntimeError):
                build_gateway()

    def test_outbox_mode_builds_queue_only_gateway(self):
        env = {"EFFECT_GATEWAY": "outbox", "DATABASE_URL": "postgresql://x/y"}
        with patch.dict(os.environ, env, clear=True):
            gateway = build_gateway()
        self.assertIs(type(gateway), OutboxGateway)

    def test_unknown_mode_is_rejected(self):
        with patch.dict(os.environ, {"EFFECT_GATEWAY": "kafka"}, clear=True):
            with self.assertRaises(RuntimeError):
                build_gateway()


if __name__ == "__main__":
    unittest.main()
