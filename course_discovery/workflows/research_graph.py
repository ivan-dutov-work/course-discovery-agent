from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.types import Overwrite, Send

from course_discovery.domain.models import RoutingAction
from course_discovery.domain.run_config import current_run_id
from course_discovery.domain.state import (
    ResearchInput,
    ResearchOutput,
    ResearchState,
)
from course_discovery.observability.logging import get_logger
from course_discovery.research_agent.cache.dedup import dedup_node
from course_discovery.research_agent.cache.nodes import (
    course_cache_lookup_node,
    course_cache_upsert_node,
)
from course_discovery.research_agent.extraction.nodes import candidate_extractor_node
from course_discovery.research_agent.memory.nodes import user_memory_lookup_node
from course_discovery.research_agent.planning.nodes import (
    replanner_node,
    research_planner_node,
)
from course_discovery.research_agent.search.nodes import tavily_search_worker_node
from course_discovery.resilience import transient_retry
from course_discovery.research_agent.synthesis.nodes import synthesizer_node
from course_discovery.research_agent.validation.nodes import (
    aggregate_node,
    evidence_validator_node,
)


logger = get_logger(__name__)


def begin_pass(state: ResearchState) -> dict:
    if state.get("routing_decision") is not None:
        return {}
    return {
        "tavily_results": Overwrite([]),
        "completed_queries": Overwrite([]),
        "research_notes": Overwrite([]),
        "research_iteration": 0,
    }


def _route_from_entry(state: ResearchState):
    if state.get("routing_decision") == RoutingAction.AUGMENT:
        return "plan_gap_search"
    return "load_user_profile"


def _dispatch_search_queries(state: ResearchState):
    if state.get("discard_reason"):
        return "discard_reason"
    plan = state.get("research_plan")
    if not plan or not plan.search_queries:
        return "merge_known_and_found_courses"

    logger.info(
        "tavily_fanout",
        extra={
            "event": "research_graph.tavily_fanout",
            "run_id": current_run_id(),
            "query_count": len(plan.search_queries),
        },
    )
    return [
        Send("search_web_for_courses", {**state, "active_search_query": query})
        for query in plan.search_queries
    ]


def build_research_graph(**compile_kwargs):
    builder = StateGraph(ResearchState, input_schema=ResearchInput, output_schema=ResearchOutput)

    retry = transient_retry()
    builder.add_node("begin_pass", begin_pass)
    builder.add_node("load_user_profile", user_memory_lookup_node, retry_policy=retry)
    builder.add_node("find_known_courses", course_cache_lookup_node, retry_policy=retry)
    builder.add_node("plan_web_search", research_planner_node)
    builder.add_node("search_web_for_courses", tavily_search_worker_node, retry_policy=retry)
    builder.add_node("extract_courses_from_results", candidate_extractor_node)
    builder.add_node("merge_known_and_found_courses", aggregate_node)
    builder.add_node("remove_duplicate_courses", dedup_node)
    builder.add_node("verify_course_claims", evidence_validator_node)
    builder.add_node("plan_gap_search", replanner_node)
    builder.add_node("save_verified_courses", course_cache_upsert_node, retry_policy=retry)
    builder.add_node("rank_and_summarize_courses", synthesizer_node)

    builder.set_entry_point("begin_pass")
    builder.add_conditional_edges(
        "begin_pass",
        _route_from_entry,
        {
            "load_user_profile": "load_user_profile",
            "plan_gap_search": "plan_gap_search",
        },
    )
    builder.add_edge("load_user_profile", "find_known_courses")
    builder.add_edge("find_known_courses", "plan_web_search")
    builder.add_conditional_edges(
        "plan_web_search",
        _dispatch_search_queries,
        {
            "merge_known_and_found_courses": "merge_known_and_found_courses",
            "discard_reason": END,
        },
    )
    builder.add_edge("search_web_for_courses", "extract_courses_from_results")
    builder.add_edge("extract_courses_from_results", "merge_known_and_found_courses")
    builder.add_edge("merge_known_and_found_courses", "remove_duplicate_courses")
    builder.add_edge("remove_duplicate_courses", "verify_course_claims")
    builder.add_conditional_edges(
        "plan_gap_search",
        _dispatch_search_queries,
        {
            "merge_known_and_found_courses": "merge_known_and_found_courses",
            "discard_reason": END,
        },
    )
    builder.add_edge("save_verified_courses", "rank_and_summarize_courses")
    builder.add_edge("rank_and_summarize_courses", END)

    return builder.compile(**compile_kwargs)
