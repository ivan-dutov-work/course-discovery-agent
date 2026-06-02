from __future__ import annotations

import time

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Send

from course_discovery.app.gateway import gateway_node
from course_discovery.domain.models import RoutingAction
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger
from course_discovery.research_agent.cache.dedup import dedup_node
from course_discovery.research_agent.search.mock_workers import (
    aggregate_node,
    worker_a_node,
    worker_b_node,
    worker_c_node,
)
from course_discovery.research_agent.synthesis.nodes import synthesizer_node
from course_discovery.review.nodes import telegram_gate_node
from course_discovery.review.router import (
    augment_dispatch_node,
    discard_node,
    publish_node,
    router_node,
)


logger = get_logger(__name__)


def _fanout_from_gateway(state: AgentState):
    run_id = state.get("run_id", "unknown")
    if state.get("error"):
        logger.warning(
            "gateway_error_route",
            extra={
                "event": "graph.gateway_error_route",
                "run_id": run_id,
                "error": state.get("error"),
            },
        )
        return "discard_node"

    base = {
        **state,
        "worker_a_courses": [],
        "worker_b_courses": [],
        "worker_c_courses": [],
    }
    logger.info(
        "gateway_fanout",
        extra={
            "event": "graph.gateway_fanout",
            "run_id": run_id,
            "targets": ["worker_a", "worker_b", "worker_c"],
        },
    )

    return [
        Send("worker_a", base),
        Send("worker_b", base),
        Send("worker_c", base),
    ]


def _fanout_from_augment(state: AgentState):
    run_id = state.get("run_id", "unknown")
    feedback = (state.get("manager_feedback") or "").lower()
    base = {
        **state,
        "worker_a_courses": [],
        "worker_b_courses": [],
        "worker_c_courses": [],
    }

    if "all" in feedback:
        logger.info(
            "augment_fanout",
            extra={
                "event": "graph.augment_fanout",
                "run_id": run_id,
                "targets": ["worker_a", "worker_b", "worker_c"],
            },
        )
        return [
            Send("worker_a", base),
            Send("worker_b", base),
            Send("worker_c", base),
        ]
    if "worker_a" in feedback or "youtube" in feedback:
        logger.info(
            "augment_fanout",
            extra={
                "event": "graph.augment_fanout",
                "run_id": run_id,
                "targets": ["worker_a"],
            },
        )
        return [Send("worker_a", base)]
    if "worker_c" in feedback or "edx" in feedback:
        logger.info(
            "augment_fanout",
            extra={
                "event": "graph.augment_fanout",
                "run_id": run_id,
                "targets": ["worker_c"],
            },
        )
        return [Send("worker_c", base)]
    logger.info(
        "augment_fanout",
        extra={
            "event": "graph.augment_fanout",
            "run_id": run_id,
            "targets": ["worker_b"],
        },
    )
    return [Send("worker_b", base)]


def build_graph():
    start_ts = time.perf_counter()
    builder = StateGraph(AgentState)

    builder.add_node("gateway", gateway_node)
    builder.add_node("worker_a", worker_a_node)
    builder.add_node("worker_b", worker_b_node)
    builder.add_node("worker_c", worker_c_node)
    builder.add_node("aggregate_workers", aggregate_node)
    builder.add_node("dedup", dedup_node)
    builder.add_node("synthesizer", synthesizer_node)
    builder.add_node("telegram_gate", telegram_gate_node)
    builder.add_node("router", router_node)
    builder.add_node("augment_dispatch", augment_dispatch_node)
    builder.add_node("publish_node", publish_node)
    builder.add_node("discard_node", discard_node)

    builder.set_entry_point("gateway")
    builder.add_conditional_edges(
        "gateway",
        _fanout_from_gateway,
        {
            "discard_node": "discard_node",
        },
    )

    builder.add_edge("worker_a", "aggregate_workers")
    builder.add_edge("worker_b", "aggregate_workers")
    builder.add_edge("worker_c", "aggregate_workers")

    builder.add_edge("aggregate_workers", "dedup")
    builder.add_edge("dedup", "synthesizer")
    builder.add_edge("synthesizer", "telegram_gate")
    builder.add_edge("telegram_gate", "router")

    builder.add_conditional_edges(
        "router",
        lambda state: state.get("routing_decision", RoutingAction.DISCARD),
        {
            RoutingAction.PUBLISH: "publish_node",
            RoutingAction.REWRITE: "synthesizer",
            RoutingAction.AUGMENT: "augment_dispatch",
            RoutingAction.RESET: "gateway",
            RoutingAction.DISCARD: "discard_node",
        },
    )

    builder.add_conditional_edges("augment_dispatch", _fanout_from_augment)

    builder.add_edge("publish_node", END)
    builder.add_edge("discard_node", END)

    graph = builder.compile(
        checkpointer=MemorySaver(),
        interrupt_before=["telegram_gate"],
    )

    logger.info(
        "graph_compiled",
        extra={
            "event": "graph.compiled",
            "node_count": 12,
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            "interrupt_before": ["telegram_gate"],
        },
    )

    return graph
