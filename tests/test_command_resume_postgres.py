from __future__ import annotations

import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from test_integration_postgres import QUERY, _durable_saver, _thread

from course_discovery.app.cli import _initial_state
from course_discovery.effects.factory import set_gateway
from course_discovery.workflows import research_graph as research_module
from course_discovery.workflows.outer_graph import build_graph

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


class _Crash(RuntimeError):
    pass


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class CommandResumeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        set_gateway(None)
        self.addCleanup(set_gateway, None)
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                "TRUNCATE recommendation_events, course_evidence, courses, pending_courses, users CASCADE"
            )
        self.events: list[str] = []

    def _instrument(self, *, crash_in: str | None):
        """Wrap the validator and replanner to log calls, crash once in `crash_in`, and demand more valid
        courses than the catalog has so the validator always routes to a replan first."""
        real_validator = research_module.evidence_validator_node
        real_replanner = research_module.replanner_node
        real_planner = research_module.research_planner_node
        crashed = {"done": False}

        def maybe_crash(name: str) -> None:
            if crash_in == name and not crashed["done"]:
                crashed["done"] = True
                self.events.append(f"{name}:crash")
                raise _Crash(name)

        def planner(state):
            update = real_planner(state)
            plan = update["research_plan"].model_copy(update={"min_valid_candidates": 15})
            return {**update, "research_plan": plan}

        def validator(state):
            maybe_crash("validator")
            self.events.append("validator")
            return real_validator(state)

        def replanner(state):
            maybe_crash("replanner")
            self.events.append("replanner")
            return real_replanner(state)

        stack = [
            patch.object(research_module, "research_planner_node", planner),
            patch.object(research_module, "evidence_validator_node", validator),
            patch.object(research_module, "replanner_node", replanner),
        ]
        for patcher in stack:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _crash_then_resume(self, thread_id: str) -> None:
        config = _thread(thread_id)
        async with _durable_saver() as saver:
            await saver.setup()
            graph = build_graph(checkpointer=saver)
            with self.assertRaises(_Crash):
                await graph.ainvoke(_initial_state(QUERY), config)
        self.events.append("--resume--")
        async with _durable_saver() as saver:
            graph = build_graph(checkpointer=saver)
            await graph.ainvoke(None, config)
            state = await graph.aget_state(config)
        self.assertEqual(state.next, ("await_human_review",))

    async def test_crash_while_the_command_node_is_pending_reruns_it_and_still_routes(self):
        self._instrument(crash_in="validator")
        await self._crash_then_resume(f"run-cmd-pending-{uuid.uuid4().hex[:8]}")

        before, after = self.events[: self.events.index("--resume--")], self.events[self.events.index("--resume--") + 1 :]
        self.assertEqual(before, ["validator:crash"])
        self.assertEqual(after[:2], ["validator", "replanner"])

    async def test_crash_after_the_command_committed_resumes_at_its_target_without_the_node(self):
        self._instrument(crash_in="replanner")
        await self._crash_then_resume(f"run-cmd-committed-{uuid.uuid4().hex[:8]}")

        split = self.events.index("--resume--")
        self.assertEqual(self.events[:split], ["validator", "replanner:crash"])
        self.assertEqual(self.events[split + 1], "replanner")


if __name__ == "__main__":
    unittest.main()
