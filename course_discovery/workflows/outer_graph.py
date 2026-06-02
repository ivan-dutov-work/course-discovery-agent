from __future__ import annotations

import time

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from course_discovery.app.gateway import gateway_node
from course_discovery.domain.models import RoutingAction
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger
from course_discovery.research_agent.memory.nodes import user_memory_update_node
from course_discovery.review.nodes import review_gate_node
from course_discovery.review.router import (
    augment_dispatch_node,
    discard_node,
    publish_node,
    router_node,
)
from course_discovery.workflows.research_graph import build_research_graph


logger = get_logger(__name__)


def _after_gateway(state: AgentState):
    if state.get("error"):
        return "discard_node"
    return "research_agent"


def _route_from_router(state: AgentState):
    return state.get("routing_decision", RoutingAction.DISCARD)


def build_graph():
    start_ts = time.perf_counter()
    builder = StateGraph(AgentState)

    builder.add_node("gateway", gateway_node)
    builder.add_node("research_agent", build_research_graph())
    builder.add_node("review_gate", review_gate_node)
    builder.add_node("router", router_node)
    builder.add_node("augment_dispatch", augment_dispatch_node)
    builder.add_node("publish_node", publish_node)
    builder.add_node("discard_node", discard_node)
    builder.add_node("user_memory_update", user_memory_update_node)

    builder.set_entry_point("gateway")
    builder.add_conditional_edges(
        "gateway",
        _after_gateway,
        {
            "research_agent": "research_agent",
            "discard_node": "discard_node",
        },
    )
    builder.add_edge("research_agent", "review_gate")
    builder.add_edge("review_gate", "router")
    builder.add_conditional_edges(
        "router",
        _route_from_router,
        {
            RoutingAction.PUBLISH: "publish_node",
            RoutingAction.REWRITE: "research_agent",
            RoutingAction.AUGMENT: "augment_dispatch",
            RoutingAction.RESET: "gateway",
            RoutingAction.DISCARD: "discard_node",
        },
    )
    builder.add_edge("augment_dispatch", "research_agent")
    builder.add_edge("publish_node", "user_memory_update")
    builder.add_edge("user_memory_update", END)
    builder.add_edge("discard_node", END)

    graph = builder.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["review_gate"],
    )

    logger.info(
        "graph_compiled",
        extra={
            "event": "outer_graph.compiled",
            "node_count": 8,
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            "interrupt_before": ["review_gate"],
        },
    )

    return graph
