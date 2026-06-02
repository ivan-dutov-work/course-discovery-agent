from __future__ import annotations

import unittest

from course_discovery.domain.models import (
    CourseCandidate,
    EvidenceItem,
    ResearchRunMetrics,
    SearchFilters,
    UserMemory,
)
from course_discovery.research_agent.cache.nodes import course_cache_lookup_node
from course_discovery.research_agent.planning.nodes import (
    replanner_node,
    research_planner_node,
)
from course_discovery.research_agent.validation.nodes import (
    aggregate_node,
    enough_valid,
    evidence_validator_node,
)


def _state(**updates):
    state = {
        "user_query": "Find free Python courses with certificate for beginners",
        "user_id": "test-user",
        "search_filters": SearchFilters(
            topic="python",
            max_price=0,
            include_certificate=True,
            level="beginner",
        ),
        "user_memory": UserMemory(),
        "cache_candidates": [],
        "research_plan": None,
        "tavily_results": [],
        "extracted_candidates": [],
        "scraped_courses": [],
        "deduplicated_courses": [],
        "valid_courses": [],
        "rejected_courses": [],
        "uncertain_courses": [],
        "validation_results": [],
        "digest": None,
        "manager_feedback": None,
        "rewrite_instructions": None,
        "routing_decision": None,
        "iteration_count": 0,
        "max_iterations": 3,
        "research_iteration": 0,
        "max_research_iterations": 2,
        "completed_queries": [],
        "research_notes": [],
        "cache_hits": 0,
        "tavily_calls": 0,
        "metrics": ResearchRunMetrics(),
        "run_id": "test-run",
        "active_search_query": None,
        "error": None,
        "published": False,
        "discard_reason": None,
    }
    state.update(updates)
    return state


class ResearchNodeTests(unittest.TestCase):
    def test_cache_lookup_uses_seed_fallback(self) -> None:
        update = course_cache_lookup_node(_state())
        self.assertGreaterEqual(len(update["cache_candidates"]), 1)
        self.assertEqual(update["cache_hits"], len(update["cache_candidates"]))

    def test_planner_searches_when_cache_is_below_target(self) -> None:
        update = research_planner_node(_state(cache_candidates=[]))
        plan = update["research_plan"]
        self.assertTrue(plan.search_queries)
        self.assertTrue(plan.use_cache_first)

    def test_validator_marks_missing_certificate_uncertain(self) -> None:
        candidate = CourseCandidate(
            title="Python Course",
            provider="example",
            url="https://example.com/python",
            source="manual",
            is_free=True,
            has_certificate=None,
            level="beginner",
            language="en",
            evidence=[
                EvidenceItem(
                    source_url="https://example.com/python",
                    quote_or_summary="Free beginner Python course.",
                    supports=["free", "beginner", "python"],
                )
            ],
            confidence=0.5,
        )
        state = _state(deduplicated_courses=[candidate])
        update = evidence_validator_node(state)
        self.assertEqual(len(update["uncertain_courses"]), 1)
        self.assertEqual(update["validation_results"][0].missing_evidence, ["certificate"])

    def test_enough_valid_routes_to_replan_until_budget_exhausted(self) -> None:
        state = _state(valid_courses=[])
        state.update(research_planner_node(state))
        self.assertEqual(enough_valid(state), "replanner")
        exhausted = _state(valid_courses=[], research_iteration=2)
        exhausted.update(research_planner_node(exhausted))
        self.assertEqual(enough_valid(exhausted), "course_cache_upsert")

    def test_replanner_avoids_completed_queries(self) -> None:
        state = _state(completed_queries=["python course beginner free certificate"])
        state.update(research_planner_node(state))
        state["validation_results"] = []
        update = replanner_node(state)
        self.assertNotIn(
            "python course beginner free certificate",
            update["research_plan"].search_queries,
        )

    def test_aggregate_merges_cache_and_extracted_candidates(self) -> None:
        course = course_cache_lookup_node(_state())["cache_candidates"][0]
        update = aggregate_node(_state(cache_candidates=[course], extracted_candidates=[]))
        self.assertEqual(update["scraped_courses"], [course])


if __name__ == "__main__":
    unittest.main()
