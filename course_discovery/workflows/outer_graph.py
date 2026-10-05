from __future__ import annotations

import time

from langgraph.graph import END, StateGraph

from course_discovery.app.gateway import gateway_node
from course_discovery.domain.models import RoutingAction
from course_discovery.domain.state import AgentState
from course_discovery.memory_curator import build_curator_graph
from course_discovery.observability.logging import get_logger
from course_discovery.persistence.checkpointer import memory_saver
from course_discovery.research_agent.cache.nodes import (
    drop_pending_courses_node,
    promote_approved_courses_node,
)
from course_discovery.research_agent.memory.nodes import user_memory_update_node
from course_discovery.review.nodes import review_gate_node
from course_discovery.review.router import (
    discard_node,
    publish_node,
    router_node,
)
from course_discovery.resilience import transient_retry
from course_discovery.workflows.research_graph import build_research_graph


logger = get_logger(__name__)


def start_research_pass(state: AgentState) -> dict:
    return {}


def _after_gateway(state: AgentState):
    if state.get("discard_reason"):
        return "discard_run"
    return "course_research"


def _route_from_router(state: AgentState):
    return state.get("routing_decision", RoutingAction.DISCARD)


def build_graph(checkpointer=None, research_compile_kwargs=None):
    start_ts = time.perf_counter()
    builder = StateGraph(AgentState)

    retry = transient_retry()
    builder.add_node("parse_user_request", gateway_node, retry_policy=retry)
    builder.add_node("start_research_pass", start_research_pass)
    builder.add_node(
        "course_research",
        build_research_graph(**{"checkpointer": True, **(research_compile_kwargs or {})}),
    )
    builder.add_node("await_human_review", review_gate_node)
    builder.add_node("interpret_review_feedback", router_node)
    builder.add_node("send_approved_courses", publish_node, retry_policy=retry)
    builder.add_node("discard_run", discard_node)
    builder.add_node("promote_approved_courses", promote_approved_courses_node, retry_policy=retry)
    builder.add_node("drop_pending_courses", drop_pending_courses_node, retry_policy=retry)
    builder.add_node("record_review_outcome", user_memory_update_node, retry_policy=retry)
    builder.add_node("curate_user_memory", build_curator_graph())

    builder.set_entry_point("parse_user_request")
    builder.add_conditional_edges(
        "parse_user_request",
        _after_gateway,
        {
            "course_research": "start_research_pass",
            "discard_run": "discard_run",
        },
    )
    builder.add_edge("start_research_pass", "course_research")
    builder.add_edge("course_research", "await_human_review")
    builder.add_edge("await_human_review", "interpret_review_feedback")
    builder.add_conditional_edges(
        "interpret_review_feedback",
        _route_from_router,
        {
            RoutingAction.PUBLISH: "send_approved_courses",
            RoutingAction.REWRITE: "start_research_pass",
            RoutingAction.AUGMENT: "start_research_pass",
            RoutingAction.RESET: "parse_user_request",
            RoutingAction.DISCARD: "discard_run",
        },
    )
    builder.add_edge("send_approved_courses", "promote_approved_courses")
    builder.add_edge("promote_approved_courses", "record_review_outcome")
    builder.add_edge("record_review_outcome", "curate_user_memory")
    builder.add_edge("curate_user_memory", END)
    builder.add_edge("discard_run", "drop_pending_courses")
    builder.add_edge("drop_pending_courses", "record_review_outcome")

    graph = builder.compile(
        checkpointer=checkpointer or memory_saver(),
        interrupt_before=["await_human_review"],
    )

    logger.info(
        "graph_compiled",
        extra={
            "event": "outer_graph.compiled",
            "node_count": 11,
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            "interrupt_before": ["await_human_review"],
        },
    )

    return graph
