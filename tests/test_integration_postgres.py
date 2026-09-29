from __future__ import annotations

import inspect
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from course_discovery.app.cli import _initial_state
from course_discovery.domain import models as domain_models
from course_discovery.research_agent.cache import nodes as cache_nodes
from course_discovery.workflows.outer_graph import build_graph

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
USER_ID = "cli-user"


QUERY = "Find free Python courses with certificate for beginners"
STATE_KEYS = ("valid_courses", "digest", "cache_hits", "tavily_calls", "routing_decision")


def _count(conn, sql: str) -> int:
    return conn.execute(sql).fetchone()[0]


def _view(values: dict) -> dict:
    return {key: values.get(key) for key in STATE_KEYS}


def _domain_model_allowlist() -> list[tuple[str, str]]:
    return [
        (cls.__module__, cls.__name__)
        for _, cls in inspect.getmembers(domain_models, inspect.isclass)
        if issubclass(cls, BaseModel) and cls.__module__ == domain_models.__name__
    ]


def _durable_saver():
    serde = JsonPlusSerializer(allowed_msgpack_modules=_domain_model_allowlist())
    return AsyncPostgresSaver.from_conn_string(TEST_DATABASE_URL, serde=serde)


def _thread(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


async def _subgraph_snapshot(graph, saver, thread_id: str, next_node: str):
    namespaces = set()
    async for saved in saver.alist(_thread(thread_id)):
        namespace = saved.config["configurable"].get("checkpoint_ns", "")
        if namespace:
            namespaces.add(namespace)
    assert len(namespaces) == 1, namespaces
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": namespaces.pop()}}
    async for snapshot in graph.aget_state_history(config):
        if snapshot.next == (next_node,):
            return snapshot
    raise AssertionError(f"no subgraph checkpoint before {next_node}")


class _UpsertCalls:
    def __init__(self):
        self.count = 0
        real = cache_nodes.upsert_courses

        def counting(*args, **kwargs):
            self.count += 1
            return real(*args, **kwargs)

        self.patcher = patch.object(cache_nodes, "upsert_courses", counting)

    def __enter__(self):
        self.patcher.start()
        return self

    def __exit__(self, *exc):
        self.patcher.stop()


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class PostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)

        self.conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.conn.close)
        self.conn.execute(
            "TRUNCATE recommendation_events, course_evidence, courses, users CASCADE"
        )
        self.conn.execute("INSERT INTO users (id) VALUES (%s)", (USER_ID,))

    async def _run_to_publish(self, run_id: str):
        graph = build_graph()
        config: RunnableConfig = {"configurable": {"thread_id": run_id}}
        query = "Find free Python courses with certificate for beginners"
        await graph.ainvoke(_initial_state(query, run_id), config)
        graph.update_state(config, {"manager_feedback": "approve"})
        result = await graph.ainvoke(None, config)
        return graph, config, result

    async def test_main_path_persists_courses_and_feedback(self):
        _, _, result = await self._run_to_publish("run-main")

        self.assertTrue(result["published"])
        self.assertGreater(len(result["valid_courses"]), 0)
        self.assertGreater(_count(self.conn, "SELECT count(*) FROM courses"), 0)
        self.assertGreater(_count(self.conn, "SELECT count(*) FROM course_evidence"), 0)
        self.assertEqual(
            _count(self.conn, "SELECT count(*) FROM recommendation_events"),
            len(result["valid_courses"]),
        )

    async def test_replay_from_checkpoint_does_not_duplicate_writes(self):
        graph, config, _ = await self._run_to_publish("run-replay")

        events = _count(self.conn, "SELECT count(*) FROM recommendation_events")
        evidence = _count(self.conn, "SELECT count(*) FROM course_evidence")
        self.assertGreater(events, 0)

        replay_points = [
            snap
            for snap in graph.get_state_history(config)
            if snap.next in {("user_memory_update",), ("research_agent",)}
        ]
        self.assertEqual(len(replay_points), 2)
        for snap in replay_points:
            await graph.ainvoke(None, snap.config)

        self.assertEqual(
            _count(self.conn, "SELECT count(*) FROM recommendation_events"), events
        )
        self.assertEqual(
            _count(self.conn, "SELECT count(*) FROM course_evidence"), evidence
        )

    async def test_replay_from_subgraph_checkpoint_reruns_only_that_node(self):
        saver = MemorySaver()
        graph = build_graph(checkpointer=saver)
        config = _thread("run-sub")
        await graph.ainvoke(_initial_state(QUERY, "run-sub"), config)
        before = _view(graph.get_state(config).values)
        events_before = _count(self.conn, "SELECT count(*) FROM course_evidence")

        snapshot = await _subgraph_snapshot(graph, saver, "run-sub", "course_cache_upsert")
        with _UpsertCalls() as calls:
            await graph.ainvoke(None, snapshot.config)

        self.assertEqual(calls.count, 1)
        self.assertEqual(graph.get_state(config).next, ("review_gate",))
        self.assertEqual(_view(graph.get_state(config).values), before)
        self.assertEqual(
            _count(self.conn, "SELECT count(*) FROM course_evidence"), events_before
        )

    async def test_durable_checkpointer_replay_from_fresh_graph_then_publish(self):
        with self.assertNoLogs("langgraph.checkpoint.serde.jsonplus", "WARNING"):
            await self._durable_replay_then_publish()

    async def _durable_replay_then_publish(self):
        thread_id = f"run-durable-{uuid.uuid4().hex[:8]}"
        config = _thread(thread_id)
        async with _durable_saver() as saver:
            await saver.setup()
            first = build_graph(checkpointer=saver)
            await first.ainvoke(_initial_state(QUERY, thread_id), config)
            before = _view((await first.aget_state(config)).values)
        evidence = _count(self.conn, "SELECT count(*) FROM course_evidence")

        async with _durable_saver() as saver:
            second = build_graph(checkpointer=saver)
            snapshot = await _subgraph_snapshot(second, saver, thread_id, "course_cache_upsert")
            with _UpsertCalls() as calls:
                await second.ainvoke(None, snapshot.config)

            self.assertEqual(calls.count, 1)
            replayed = await second.aget_state(config)
            self.assertEqual(replayed.next, ("review_gate",))
            self.assertEqual(_view(replayed.values), before)
            self.assertEqual(
                _count(self.conn, "SELECT count(*) FROM course_evidence"), evidence
            )

            await second.aupdate_state(config, {"manager_feedback": "approve"})
            result = await second.ainvoke(None, config)

        self.assertTrue(result["published"])
        self.assertEqual(
            _count(self.conn, "SELECT count(*) FROM recommendation_events"),
            len(result["valid_courses"]),
        )

    async def test_interrupt_inside_subgraph_pauses_resumes_and_replays(self):
        saver = MemorySaver()
        graph = build_graph(
            checkpointer=saver,
            research_compile_kwargs={"interrupt_before": ["course_cache_upsert"]},
        )
        config = _thread("run-mid")
        await graph.ainvoke(_initial_state(QUERY, "run-mid"), config)

        paused = graph.get_state(config, subgraphs=True)
        self.assertEqual(paused.next, ("research_agent",))
        self.assertEqual(paused.tasks[0].state.next, ("course_cache_upsert",))
        self.assertEqual(_count(self.conn, "SELECT count(*) FROM courses"), 0)

        await graph.ainvoke(None, config)
        self.assertEqual(graph.get_state(config).next, ("review_gate",))
        self.assertGreater(_count(self.conn, "SELECT count(*) FROM courses"), 0)
        evidence = _count(self.conn, "SELECT count(*) FROM course_evidence")

        snapshot = await _subgraph_snapshot(graph, saver, "run-mid", "course_cache_upsert")
        await graph.ainvoke(None, snapshot.config)
        self.assertEqual(
            _count(self.conn, "SELECT count(*) FROM course_evidence"), evidence
        )


if __name__ == "__main__":
    unittest.main()
