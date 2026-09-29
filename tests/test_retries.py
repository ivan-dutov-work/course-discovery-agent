from __future__ import annotations

import asyncio
import os
import unittest
from collections import Counter
from unittest.mock import patch

import httpx
import psycopg
from langchain_core.runnables import RunnableConfig

from course_discovery.app import gateway as gateway_module
from course_discovery.app.cli import _initial_state
from course_discovery.app.llm import build_llm
from course_discovery.domain.models import DeliveryStatus, RoutingAction, SearchFilters
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence import postgres
from course_discovery.persistence.checkpointer import memory_saver
from course_discovery.research_agent.cache import nodes as cache_nodes
from course_discovery.research_agent.memory import nodes as memory_nodes
from course_discovery.research_agent.search import nodes as search_nodes
from course_discovery.research_agent.search.tavily_client import TavilyClient
from course_discovery.resilience import RETRY_SETTINGS, is_transient
from course_discovery.workflows.outer_graph import build_graph
from course_discovery.workflows.research_graph import build_research_graph

QUERY = "Find free Python courses with certificate for beginners"
FAST_RETRY = {**RETRY_SETTINGS, "initial_interval": 0.0, "jitter": False}


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"http {status_code}")
        self.status_code = status_code


def _config(run_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": run_id}}


class TransientClassificationTests(unittest.TestCase):
    def test_transient_errors(self):
        for exc in (
            TimeoutError(),
            asyncio.TimeoutError(),
            ConnectionResetError(),
            httpx.ReadTimeout("slow"),
            httpx.ConnectError("refused"),
            psycopg.OperationalError("server closed the connection"),
            _StatusError(429),
            _StatusError(503),
            _StatusError(408),
        ):
            self.assertTrue(is_transient(exc), repr(exc))

    def test_permanent_errors(self):
        for exc in (
            ValueError("bad"),
            KeyError("x"),
            psycopg.errors.CheckViolation("nope"),
            psycopg.errors.UniqueViolation("dup"),
            _StatusError(400),
            _StatusError(401),
            _StatusError(404),
        ):
            self.assertFalse(is_transient(exc), repr(exc))


class GraphRetryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        settings = patch.dict(RETRY_SETTINGS, FAST_RETRY)
        settings.start()
        self.addCleanup(settings.stop)
        store = InMemoryOutboxStore()
        self.handled: list[str] = []
        worker = OutboxWorker(store, {"publish_digest": lambda e: self.handled.append(e.key)})
        set_gateway(InlineGateway(store, worker))
        self.addCleanup(set_gateway, None)

    def flaky_parse(self, failures: list[Exception]):
        calls = Counter()

        def parse(query: str) -> SearchFilters:
            calls["n"] += 1
            if failures:
                raise failures.pop(0)
            return SearchFilters(topic="python", max_price=0.0)

        return parse, calls

    async def test_gateway_recovers_from_transient_failures(self):
        parse, calls = self.flaky_parse([ConnectionError("a"), TimeoutError("b")])
        with patch.object(gateway_module, "_parse_filters", parse):
            await build_graph().ainvoke(_initial_state(QUERY, "r-recover"), _config("r-recover"))

        self.assertEqual(calls["n"], 3)

    async def test_gateway_gives_up_after_max_attempts(self):
        parse, calls = self.flaky_parse([ConnectionError("down")] * 10)
        graph = build_graph()
        with patch.object(gateway_module, "_parse_filters", parse):
            with self.assertRaises(ConnectionError):
                await graph.ainvoke(_initial_state(QUERY, "r-giveup"), _config("r-giveup"))

        self.assertEqual(calls["n"], RETRY_SETTINGS["max_attempts"])

    async def test_gateway_permanent_failure_is_not_retried_and_discards(self):
        parse, calls = self.flaky_parse([ValueError("unparseable")])
        with patch.object(gateway_module, "_parse_filters", parse):
            result = await build_graph().ainvoke(
                _initial_state(QUERY, "r-perm"), _config("r-perm")
            )

        self.assertEqual(calls["n"], 1)
        self.assertEqual(result["routing_decision"], RoutingAction.DISCARD)
        self.assertIn("Gateway parsing failed", result["error"])

    async def _resume_after_search_outage(self, graph, state, run_id):
        real = TavilyClient.search
        calls = Counter()
        broken = {"active": True}
        poisoned: list[str] = []

        async def search(self, query, *, max_results=5):
            calls[query] += 1
            if not poisoned:
                poisoned.append(query)
            if query == poisoned[0] and broken["active"]:
                raise ConnectionError("search backend down")
            return await real(self, query, max_results=max_results)

        config = _config(run_id)
        with patch.object(TavilyClient, "search", search):
            with self.assertRaises(ConnectionError):
                await graph.ainvoke(state, config)
            after_failure = dict(calls)
            broken["active"] = False
            await graph.ainvoke(None, config)

        self.assertEqual(after_failure[poisoned[0]], RETRY_SETTINGS["max_attempts"])
        self.assertEqual(calls[poisoned[0]], RETRY_SETTINGS["max_attempts"] + 1)
        siblings = {q: n for q, n in calls.items() if q != poisoned[0]}
        self.assertTrue(siblings)
        self.assertTrue(all(after_failure[q] == 1 for q in siblings))
        return siblings

    async def test_standalone_graph_resume_keeps_completed_sibling_writes(self):
        state = _initial_state(QUERY, "r-flat")
        state.update(gateway_module.gateway_node(state))

        siblings = await self._resume_after_search_outage(
            build_research_graph(checkpointer=memory_saver()), state, "r-flat"
        )

        self.assertTrue(all(n == 1 for n in siblings.values()), siblings)

    async def test_subgraph_resume_reruns_completed_sibling(self):
        siblings = await self._resume_after_search_outage(
            build_graph(), _initial_state(QUERY, "r-nested"), "r-nested"
        )

        self.assertTrue(all(n == 2 for n in siblings.values()), siblings)

    async def test_search_timeout_is_retried_as_transient(self):
        calls = Counter()

        async def hang(self, query, *, max_results=5):
            calls[query] += 1
            await asyncio.sleep(5)

        with patch.object(TavilyClient, "search", hang), patch.object(
            search_nodes, "SEARCH_TIMEOUT_SECONDS", 0.01
        ):
            with self.assertRaises(TimeoutError):
                await build_graph().ainvoke(_initial_state(QUERY, "r-hang"), _config("r-hang"))

        self.assertTrue(all(n == RETRY_SETTINGS["max_attempts"] for n in calls.values()))

    async def test_search_permanent_error_is_recorded_not_retried(self):
        calls = Counter()

        async def broken(self, query, *, max_results=5):
            calls[query] += 1
            raise ValueError("bad response shape")

        with patch.object(TavilyClient, "search", broken):
            result = await build_graph().ainvoke(
                _initial_state(QUERY, "r-badsearch"), _config("r-badsearch")
            )

        self.assertTrue(all(n == 1 for n in calls.values()))
        self.assertTrue(any("Tavily search failed" in n for n in result["research_notes"]))

    async def test_feedback_write_is_retried_on_transient_db_error_only(self):
        calls = Counter()
        failures = [psycopg.OperationalError("connection lost")] * 2

        def record(*args, **kwargs):
            calls["n"] += 1
            if failures:
                raise failures.pop(0)

        graph = build_graph()
        config = _config("r-write")
        await graph.ainvoke(_initial_state(QUERY, "r-write"), config)
        graph.update_state(config, {"manager_feedback": "approve"})
        with patch.object(memory_nodes, "record_feedback", record):
            result = await graph.ainvoke(None, config)

        self.assertEqual(calls["n"], 3)
        self.assertEqual(result["publish_status"], DeliveryStatus.DELIVERED)

    async def test_feedback_write_integrity_error_is_not_retried(self):
        calls = Counter()

        def record(*args, **kwargs):
            calls["n"] += 1
            raise psycopg.errors.CheckViolation("rejected")

        graph = build_graph()
        config = _config("r-integrity")
        await graph.ainvoke(_initial_state(QUERY, "r-integrity"), config)
        graph.update_state(config, {"manager_feedback": "approve"})
        with patch.object(memory_nodes, "record_feedback", record):
            with self.assertRaises(psycopg.errors.CheckViolation):
                await graph.ainvoke(None, config)

        self.assertEqual(calls["n"], 1)

    async def test_memory_read_is_retried_on_transient_db_error(self):
        calls = Counter()
        failures = [psycopg.OperationalError("connection lost")] * 2
        real = memory_nodes.load_user_memory

        def load(user_id):
            calls["n"] += 1
            if failures:
                raise failures.pop(0)
            return real(user_id)

        with patch.object(memory_nodes, "load_user_memory", load):
            await build_graph().ainvoke(_initial_state(QUERY, "r-read"), _config("r-read"))

        self.assertEqual(calls["n"], 3)

    async def test_cache_read_failure_propagates_instead_of_returning_empty(self):
        calls = Counter()

        def search(*args, **kwargs):
            calls["n"] += 1
            raise psycopg.errors.UndefinedTable("courses")

        with patch.object(cache_nodes, "search_course_cache", search):
            with self.assertRaises(psycopg.errors.UndefinedTable):
                await build_graph().ainvoke(
                    _initial_state(QUERY, "r-cache-read"), _config("r-cache-read")
                )

        self.assertEqual(calls["n"], 1)

    async def test_publish_submit_is_retried_and_delivers_once(self):
        store = InMemoryOutboxStore()
        inner = InlineGateway(
            store, OutboxWorker(store, {"publish_digest": lambda e: self.handled.append(e.key)})
        )
        attempts = Counter()

        class FlakySubmit:
            def submit(self, effect):
                attempts["n"] += 1
                if attempts["n"] < 3:
                    raise psycopg.OperationalError("outbox unreachable")
                return inner.submit(effect)

            def status(self, key):
                return inner.status(key)

        set_gateway(FlakySubmit())
        graph = build_graph()
        config = _config("r-submit")
        await graph.ainvoke(_initial_state(QUERY, "r-submit"), config)
        graph.update_state(config, {"manager_feedback": "approve"})

        result = await graph.ainvoke(None, config)

        self.assertEqual(attempts["n"], 3)
        self.assertEqual(result["publish_status"], DeliveryStatus.DELIVERED)
        self.assertEqual(self.handled, ["publish:r-submit"])


class TimeoutConfigTests(unittest.TestCase):
    def test_llm_client_has_a_timeout_and_no_hidden_retries_by_default(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "k"}):
            default = build_llm("gateway")
            tolerant = build_llm("router", max_retries=2)

        self.assertEqual(default.request_timeout, 30_000)
        self.assertEqual(default.max_retries, 0)
        self.assertEqual(tolerant.max_retries, 2)

    def test_database_connections_carry_connect_and_statement_timeouts(self):
        with patch("psycopg.connect") as connect:
            postgres.open_connection("postgresql://x/y")

        kwargs = connect.call_args.kwargs
        self.assertEqual(kwargs["connect_timeout"], 5)
        self.assertEqual(kwargs["options"], "-c statement_timeout=15000")


if __name__ == "__main__":
    unittest.main()
