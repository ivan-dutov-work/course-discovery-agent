from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.types import Send

from course_discovery.domain.models import RoutingAction
from course_discovery.domain.state import AgentState
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
from course_discovery.research_agent.synthesis.nodes import synthesizer_node
from course_discovery.research_agent.validation.nodes import (
    aggregate_node,
    enough_valid,
    evidence_validator_node,
)


logger = get_logger(__name__)


def research_entry_node(_: AgentState) -> dict:
    return {}


def _route_from_entry(state: AgentState):
    if state.get("routing_decision") == RoutingAction.AUGMENT:
        return "replanner"
    return "user_memory_lookup"


def _dispatch_search_queries(state: AgentState):
    if state.get("error"):
        return "research_done"
    plan = state.get("research_plan")
    if not plan or not plan.search_queries:
        return "aggregate"

    logger.info(
        "tavily_fanout",
        extra={
            "event": "research_graph.tavily_fanout",
            "run_id": state.get("run_id", "unknown"),
            "query_count": len(plan.search_queries),
        },
    )
    return [
        Send("tavily_search_worker", {**state, "active_search_query": query})
        for query in plan.search_queries
    ]


def build_research_graph():
    builder = StateGraph(AgentState)

    builder.add_node("research_entry", research_entry_node)
    builder.add_node("user_memory_lookup", user_memory_lookup_node)
    builder.add_node("course_cache_lookup", course_cache_lookup_node)
    builder.add_node("research_planner", research_planner_node)
    builder.add_node("tavily_search_worker", tavily_search_worker_node)
    builder.add_node("candidate_extractor", candidate_extractor_node)
    builder.add_node("aggregate", aggregate_node)
    builder.add_node("dedup", dedup_node)
    builder.add_node("evidence_validator", evidence_validator_node)
    builder.add_node("replanner", replanner_node)
    builder.add_node("course_cache_upsert", course_cache_upsert_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("research_done", lambda _: {})

    builder.set_entry_point("research_entry")
    builder.add_conditional_edges(
        "research_entry",
        _route_from_entry,
        {
            "user_memory_lookup": "user_memory_lookup",
            "replanner": "replanner",
        },
    )
    builder.add_edge("user_memory_lookup", "course_cache_lookup")
    builder.add_edge("course_cache_lookup", "research_planner")
    builder.add_conditional_edges(
        "research_planner",
        _dispatch_search_queries,
        {
            "aggregate": "aggregate",
            "research_done": "research_done",
        },
    )
    builder.add_edge("tavily_search_worker", "candidate_extractor")
    builder.add_edge("candidate_extractor", "aggregate")
    builder.add_edge("aggregate", "dedup")
    builder.add_edge("dedup", "evidence_validator")
    builder.add_conditional_edges(
        "evidence_validator",
        enough_valid,
        {
            "course_cache_upsert": "course_cache_upsert",
            "replanner": "replanner",
        },
    )
    builder.add_conditional_edges(
        "replanner",
        _dispatch_search_queries,
        {
            "aggregate": "aggregate",
            "research_done": "research_done",
        },
    )
    builder.add_edge("course_cache_upsert", "synthesizer")
    builder.add_edge("synthesizer", END)
    builder.add_edge("research_done", END)

    return builder.compile()
