from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import RoutingAction
from course_discovery.privacy.flow_specs import FLOWS
from course_discovery.workflows.outer_graph import build_graph
from course_discovery.workflows.research_graph import build_research_graph

QUERY = "Find free Python courses with certificate for beginners"

OUTER_NODES = {
    "parse_user_request",
    "course_research",
    "await_human_review",
    "interpret_review_feedback",
    "send_approved_courses",
    "discard_run",
    "record_review_outcome",
    "curate_user_memory",
}
RESEARCH_NODES = {
    "load_user_profile",
    "find_known_courses",
    "plan_web_search",
    "search_web_for_courses",
    "extract_courses_from_results",
    "merge_known_and_found_courses",
    "remove_duplicate_courses",
    "verify_course_claims",
    "plan_gap_search",
    "save_verified_courses",
    "rank_and_summarize_courses",
}
REMOVED = {"start_research", "prepare_augmented_search", "end_research_on_error"}


def _real_nodes(compiled) -> set[str]:
    return {name for name in compiled.get_graph().nodes if not name.startswith("__")}


def _edges(compiled) -> set[tuple[str, str]]:
    return {(e.source, e.target) for e in compiled.get_graph().edges}


class TopologyTests(unittest.TestCase):
    def test_outer_graph_node_set_is_exact(self):
        self.assertEqual(_real_nodes(build_graph()), OUTER_NODES)

    def test_research_graph_node_set_is_exact(self):
        self.assertEqual(_real_nodes(build_research_graph()), RESEARCH_NODES)

    def test_removed_anchors_are_gone_everywhere(self):
        self.assertFalse(REMOVED & _real_nodes(build_graph()))
        self.assertFalse(REMOVED & _real_nodes(build_research_graph()))
        self.assertFalse(REMOVED & set(FLOWS))

    def test_interrupt_before_is_only_the_review_gate(self):
        self.assertEqual(list(build_graph().interrupt_before_nodes), ["await_human_review"])

    def test_discard_is_recorded_before_the_end(self):
        edges = _edges(build_graph())
        self.assertIn(("discard_run", "record_review_outcome"), edges)
        self.assertNotIn(("discard_run", "__end__"), edges)

    def test_curator_runs_after_the_outcome_is_recorded_and_before_the_end(self):
        edges = _edges(build_graph())
        self.assertIn(("record_review_outcome", "curate_user_memory"), edges)
        self.assertIn(("curate_user_memory", "__end__"), edges)
        self.assertNotIn(("record_review_outcome", "__end__"), edges)

    def test_augment_edge_goes_straight_to_research(self):
        self.assertIn(("interpret_review_feedback", "course_research"), _edges(build_graph()))

    def test_research_entry_is_conditional(self):
        edges = _edges(build_research_graph())
        self.assertIn(("__start__", "load_user_profile"), edges)
        self.assertIn(("__start__", "plan_gap_search"), edges)


class TopologyRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)

    async def _visited(self, graph, inputs, config) -> list[str]:
        visited: list[str] = []
        async for _, chunk in graph.astream(inputs, config, stream_mode="updates", subgraphs=True):
            visited.extend(name for name in chunk if not name.startswith("__"))
        return visited

    async def test_augment_reenters_research_at_gap_planning(self):
        graph = build_graph()
        config = {"configurable": {"thread_id": "topo-augment"}}
        await self._visited(graph, _initial_state(QUERY, "topo-augment"), config)

        await graph.aupdate_state(
            config,
            {"routing_decision": RoutingAction.AUGMENT, "manager_feedback": "find more"},
            as_node="interpret_review_feedback",
        )
        visited = await self._visited(graph, None, config)

        self.assertEqual(visited[0], "plan_gap_search")
        self.assertNotIn("load_user_profile", visited)
        self.assertNotIn("find_known_courses", visited)
        self.assertEqual((await graph.aget_state(config)).next, ("await_human_review",))

    async def test_parse_error_ends_in_discard_run(self):
        graph = build_graph()
        config = {"configurable": {"thread_id": "topo-parse-error"}}
        state = _initial_state(QUERY, "topo-parse-error")
        del state["user_query"]

        visited = await self._visited(graph, state, config)

        self.assertEqual(
            visited,
            ["parse_user_request", "discard_run", "record_review_outcome", "load_context", "curate_user_memory"],
        )
        self.assertEqual((await graph.aget_state(config)).next, ())

    async def test_planning_error_ends_research_without_dangling_node(self):
        graph = build_research_graph()
        state = _initial_state(QUERY, "topo-plan-error")

        visited = await self._visited(graph, state, None)
        result = await graph.ainvoke(state)

        self.assertEqual(visited, ["load_user_profile", "find_known_courses", "plan_web_search"])
        self.assertIn("search filters missing", result["error"])
        self.assertFalse(result["digest"])

    async def test_search_failure_leaves_a_limitation_note_and_finishes(self):
        graph = build_graph()
        config = {"configurable": {"thread_id": "topo-search-error"}}

        with patch(
            "course_discovery.research_agent.search.nodes.TavilyClient.search",
            side_effect=ValueError("boom"),
        ):
            visited = await self._visited(graph, _initial_state(QUERY, "topo-search-error"), config)

        values = (await graph.aget_state(config)).values
        self.assertIn("rank_and_summarize_courses", visited)
        self.assertTrue(any("search failed" in note for note in values["research_notes"]))
        self.assertFalse(values.get("error"))
        self.assertEqual((await graph.aget_state(config)).next, ("await_human_review",))


if __name__ == "__main__":
    unittest.main()
