from __future__ import annotations

import operator
import os
import typing
import unittest
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph

from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import ResearchRunMetrics, RoutingAction
from course_discovery.domain.state import AgentState, ResearchInput, ResearchOutput, ResearchState
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.research_agent.search.tavily_client import TavilyClient
from course_discovery.workflows.outer_graph import MAX_STALE_RETRIES, StaleResearchResultError, build_graph
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

    async def _visited(self, inputs, config) -> list[str]:
        visited: list[str] = []
        async for _, chunk in self.graph.astream(inputs, config, stream_mode="updates", subgraphs=True):
            visited.extend(name for name in chunk if not name.startswith("__"))
        return visited

    async def test_reset_starts_the_subgraph_from_empty_private_state(self):
        config, first = await self._first_pass("echo-reset")
        self.assertTrue(first["tavily_results"])
        self.searched.clear()

        await self.graph.aupdate_state(
            config,
            {"routing_decision": RoutingAction.RESET, "feedback_history": ["reset: I want free Rust courses"]},
            as_node="interpret_review_feedback",
        )
        await self._drain(None, config)
        values = await research_values(self.graph, "echo-reset")

        self.assertTrue(self.searched)
        self.assertEqual(values["completed_queries"], self.searched)
        self.assertEqual(len(values["tavily_results"]), len(self.searched) * 5)
        self.assertEqual(values["research_iteration"], 0)

    async def test_crash_after_the_pass_marker_still_runs_the_requested_round(self):
        config, first = await self._first_pass("echo-stale-window")

        await self.graph.aupdate_state(
            config,
            {
                "routing_decision": RoutingAction.AUGMENT,
                "feedback_history": ["augment: more courses"],
                "research_pass": 1,
            },
            as_node="start_research_pass",
        )
        visited = await self._visited(None, config)

        self.assertIn("plan_gap_search", visited)
        self.assertEqual((await self.graph.aget_state(config)).values["research_pass"], 1)
        self.assertEqual(visited.count("retry_research_pass"), 1)
        self.assertEqual((await self.graph.aget_state(config)).values["research_retries"], 1)
        self.assertEqual((await self.graph.aget_state(config)).next, ("await_human_review",))

    async def test_planning_failure_reaches_the_outer_graph_and_discards(self):
        with patch("course_discovery.workflows.outer_graph.gateway_node", lambda state: {"search_filters": None}):
            graph = build_graph()
        config = _config("echo-plan-error")

        visited = []
        async for _, chunk in graph.astream(_initial_state(QUERY), config, stream_mode="updates", subgraphs=True):
            visited.extend(name for name in chunk if not name.startswith("__"))
        state = await graph.aget_state(config)

        self.assertIn("discard_run", visited)
        self.assertNotIn("await_human_review", visited)
        self.assertIn("search filters missing", state.values["discard_reason"])
        self.assertEqual(state.next, ())


class StaleResultBoundTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.runs = 0

        def always_stale(state):
            self.runs += 1
            return {
                "research_pass": -1,
                "valid_courses": [],
                "digest": "stale",
                "metrics": ResearchRunMetrics(),
                "discard_reason": None,
            }

        stub = StateGraph(ResearchState, input_schema=ResearchInput, output_schema=ResearchOutput)
        stub.add_node("only", always_stale)
        stub.add_edge(START, "only")
        stub.add_edge("only", END)
        patcher = patch("course_discovery.workflows.outer_graph.build_research_graph", lambda **_: stub.compile())
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_result_that_stays_stale_fails_after_the_bounded_retries(self):
        graph = build_graph()
        with self.assertRaises(StaleResearchResultError):
            async for _ in graph.astream(_initial_state(QUERY), _config("stale-bound")):
                pass
        self.assertEqual(self.runs, 1 + MAX_STALE_RETRIES)


def _reduced(schema) -> set[str]:
    hints = typing.get_type_hints(schema, include_extras=True)
    return {
        name
        for name, hint in hints.items()
        if operator.add in getattr(hint, "__metadata__", ())
    }


class ReducerPlacementTests(unittest.TestCase):
    def test_the_outer_state_reduces_only_the_review_history(self):
        self.assertEqual(_reduced(AgentState), {"feedback_history"})

    def test_every_channel_written_by_a_send_branch_has_a_reducer_on_the_subgraph(self):
        from course_discovery.privacy.flow_specs import RESEARCH

        fanned_out = RESEARCH["search_web_for_courses"].writes
        self.assertEqual(fanned_out & set(ResearchState.__annotations__), fanned_out)
        self.assertLessEqual(fanned_out, _reduced(ResearchState) | {"metrics"})
