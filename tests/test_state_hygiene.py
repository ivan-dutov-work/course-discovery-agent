from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from course_discovery.app.cli import _initial_state
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.research_agent.search.tavily_client import TavilyClient
from course_discovery.workflows.outer_graph import build_graph
from tests.research_view import research_values

QUERY = "Find free Python courses with certificate for beginners"


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}


class ReducerEchoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))
        self.searched: list[str] = []
        original = TavilyClient.search

        async def counting(client, query, *args, **kwargs):
            self.searched.append(query)
            return await original(client, query, *args, **kwargs)

        patcher = patch.object(TavilyClient, "search", counting)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.graph = build_graph()

    async def _drain(self, inputs, config) -> None:
        async for _ in self.graph.astream(inputs, config):
            pass

    async def _round(self, config, feedback: str) -> dict:
        await self.graph.aupdate_state(config, {"manager_feedback": feedback})
        await self._drain(None, config)
        return await research_values(self.graph, config["configurable"]["thread_id"])

    async def _first_pass(self, thread_id: str) -> tuple[dict, dict]:
        config = _config(thread_id)
        await self._drain(_initial_state(QUERY), config)
        return config, await research_values(self.graph, thread_id)

    async def test_rewrite_rounds_do_not_re_add_the_ledger(self):
        config, first = await self._first_pass("echo-rewrite")
        ledger = list(first["completed_queries"])
        self.assertEqual(len(ledger), len(self.searched))
        self.assertEqual(len(ledger), len(set(ledger)))

        for _ in range(2):
            values = await self._round(config, "rewrite: shorter digest please")
            self.assertEqual(values["completed_queries"], ledger)
            self.assertEqual(values["metrics"].tavily_calls, len(ledger))
            self.assertEqual(len(values["tavily_results"]), len(first["tavily_results"]))
            self.assertEqual(values.get("research_notes"), first.get("research_notes"))

        self.assertEqual(len(self.searched), len(ledger))

    async def test_augment_rounds_add_only_the_queries_actually_run(self):
        config, first = await self._first_pass("echo-augment")
        previous = list(first["completed_queries"])

        for _ in range(2):
            values = await self._round(config, "augment: more courses")
            ledger = values["completed_queries"]
            self.assertEqual(ledger[: len(previous)], previous)
            self.assertEqual(len(ledger), len(set(ledger)))
            self.assertEqual(len(ledger), len(self.searched))
            self.assertEqual(values["metrics"].tavily_calls, len(ledger))
            previous = list(ledger)
