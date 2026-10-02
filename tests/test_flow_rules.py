from __future__ import annotations

import dataclasses
import os
import unittest
from unittest.mock import patch

from langchain_core.runnables import RunnableConfig

from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import UserMemory
from course_discovery.domain.state import AgentState
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.research_agent.memory import nodes as memory_nodes
from course_discovery.privacy.flow import NodeFlow, Violation, check_flows, pii_seeds, propagate
from course_discovery.privacy.flow_specs import EDGES, FLOWS
from course_discovery.privacy.sources import NOT_USER_DATA, USER_DATA_SOURCES
from course_discovery.memory_curator import build_curator_graph
from course_discovery.workflows.outer_graph import build_graph
from course_discovery.workflows.research_graph import build_research_graph

QUERY = "Find free Python courses with certificate for beginners"
CHANNELS = set(AgentState.__annotations__)


def _graph_nodes() -> set[str]:
    names = (
        set(build_graph().get_graph().nodes)
        | set(build_research_graph().get_graph().nodes)
        | set(build_curator_graph().get_graph().nodes)
    )
    return names - {"__start__", "__end__"}


def _check(flows: dict[str, NodeFlow]) -> list[Violation]:
    return check_flows(
        flows,
        pii_seeds(AgentState),
        graph_nodes=_graph_nodes(),
        channels=CHANNELS,
        erasable_stores={source.table for source in USER_DATA_SOURCES},
        unerasable_stores=set(NOT_USER_DATA),
    )


def _with(node: str, **changes) -> dict[str, NodeFlow]:
    return {**FLOWS, node: dataclasses.replace(FLOWS[node], **changes)}


class FlowRuleTests(unittest.TestCase):
    def test_seeds_come_from_state_annotations(self):
        self.assertEqual(
            set(pii_seeds(AgentState)),
            {"user_query", "user_memory", "manager_feedback", "feedback_history"},
        )

    def test_declared_flows_are_clean(self):
        self.assertEqual(_check(FLOWS), [])

    def test_taint_reaches_digest_through_research_notes(self):
        labels, origins = propagate(FLOWS, pii_seeds(AgentState))
        self.assertIn("subject", labels["digest"])
        self.assertNotIn("raw", labels["digest"])
        self.assertIn("subject", labels["active_search_query"])

    def test_missing_declaration_fails_coverage(self):
        flows = {name: spec for name, spec in FLOWS.items() if name != "remove_duplicate_courses"}
        self.assertEqual([v.rule for v in _check(flows)], ["coverage"])

    def test_typo_in_channel_name_is_caught(self):
        flows = _with("remove_duplicate_courses", reads=frozenset({"scraped_course"}))
        self.assertEqual([v.rule for v in _check(flows)], ["unknown-channel"])

    def test_removing_a_declassification_reaches_the_catalog_tables(self):
        spec = FLOWS["verify_course_claims"]
        declassifies = {k: v for k, v in spec.declassifies.items() if k != "valid_courses"}
        rules = {(v.rule, v.node) for v in _check(_with("verify_course_claims", declassifies=declassifies))}
        self.assertIn(("erasable", "save_verified_courses"), rules)
        self.assertIn(("sink-accepts", "save_verified_courses"), rules)

    def test_raw_memory_reaching_the_llm_is_caught(self):
        flows = _with("rank_and_summarize_courses", redacts=frozenset())
        messages = [str(v) for v in _check(flows) if v.rule == "sink-accepts"]
        self.assertTrue(any("'raw' data reaches external sink 'llm:openrouter'" in m for m in messages))

    def test_redaction_declared_on_the_node_clears_raw(self):
        flows = _with(
            "rank_and_summarize_courses",
            reads=FLOWS["rank_and_summarize_courses"].reads | {"user_memory"},
            redacts=frozenset({"user_memory"}),
        )
        self.assertEqual(_check(flows), [])

    def test_new_store_must_be_erasable(self):
        from course_discovery.privacy.flow import store

        flows = _with("record_review_outcome", sinks=(store("notes"),))
        self.assertIn("erasable", {v.rule for v in _check(flows)})


class DeclaredWritesMatchRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        store = InMemoryOutboxStore()
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": lambda _: None})))

    async def test_nodes_write_only_declared_channels(self):
        graph = build_graph()
        config: RunnableConfig = {"configurable": {"thread_id": "flow-run"}}
        seen: dict[str, set[str]] = {}

        async def drain(stream):
            async for namespace, chunk in stream:
                for node, update in chunk.items():
                    if node.startswith("__") or not isinstance(update, dict):
                        continue
                    if node in {"course_research", "curate_user_memory"} and not namespace:
                        continue
                    seen.setdefault(node, set()).update(update)

        await drain(graph.astream(_initial_state(QUERY, "flow-run"), config, stream_mode="updates", subgraphs=True))
        graph.update_state(config, {"manager_feedback": "approve"})
        await drain(graph.astream(None, config, stream_mode="updates", subgraphs=True))

        self.assertIn("verify_course_claims", seen)
        self.assertIn("send_approved_courses", seen)
        for node, written in seen.items():
            self.assertLessEqual(written, FLOWS[node].writes, node)

    def test_edge_pseudo_nodes_are_not_graph_nodes(self):
        self.assertFalse(EDGES & _graph_nodes())


SUBJECT_CANARY = "zebra7431"
RAW_CANARY = "quokka5820"


class CanaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)
        self.addCleanup(set_gateway, None)
        self.delivered: list = []
        store = InMemoryOutboxStore()
        handler = self.delivered.append
        set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": handler})))

    async def _run(self) -> dict[str, str]:
        memory = UserMemory(career_goals=[f"become a {RAW_CANARY} trainer"])
        graph = build_graph()
        config: RunnableConfig = {"configurable": {"thread_id": "canary-run"}}
        with patch.object(memory_nodes, "load_user_memory", return_value=memory):
            await graph.ainvoke(_initial_state(f"{QUERY} {SUBJECT_CANARY}", "canary-run"), config)
            graph.update_state(config, {"manager_feedback": "approve"})
            await graph.ainvoke(None, config)
        seen: dict[str, str] = {}
        async for snapshot in graph.aget_state_history(config):
            for channel, value in snapshot.values.items():
                seen[channel] = seen.get(channel, "") + repr(value)
        return seen

    async def test_canaries_stay_inside_labelled_channels(self):
        seen = await self._run()
        labels, _ = propagate(FLOWS, pii_seeds(AgentState))

        holding_subject = {c for c, text in seen.items() if SUBJECT_CANARY in text}
        holding_raw = {c for c, text in seen.items() if RAW_CANARY in text}

        self.assertIn("user_query", holding_subject)
        self.assertGreater(len(holding_subject), 1, "canary never propagated")
        self.assertEqual({c for c in holding_subject if "subject" not in labels.get(c, ())}, set())
        self.assertEqual({c for c in holding_raw if "raw" not in labels.get(c, ())}, set())
        self.assertNotIn("user_query", holding_raw)

    async def test_raw_canary_never_reaches_the_outbox(self):
        await self._run()
        self.assertTrue(self.delivered)
        self.assertNotIn(RAW_CANARY, repr(self.delivered[0].payload))
        self.assertIn(SUBJECT_CANARY, repr(self.delivered[0].payload))
