from __future__ import annotations

import os
import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agent.logging_utils import get_logger, sanitize_error, truncate_text
from agent.models import RoutingAction, RoutingDecision
from agent.prompts import ROUTER_SYSTEM_PROMPT
from agent.state import AgentState


logger = get_logger(__name__)


def _coerce_routing_decision(value: Any) -> RoutingDecision:
    if isinstance(value, RoutingDecision):
        return value
    if isinstance(value, dict):
        return RoutingDecision.model_validate(value)
    return RoutingDecision.model_validate(getattr(value, "model_dump", lambda: value)())


def _build_router_llm() -> ChatGoogleGenerativeAI:
    if not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY is required for router node")
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)


def router_node(state: AgentState) -> dict:
    run_id = state.get("run_id", "unknown")
    feedback = (state.get("manager_feedback") or "").strip()
    logger.info(
        "router_decision_start",
        extra={
            "event": "router.decision_start",
            "run_id": run_id,
            "iteration_count": state.get("iteration_count", 0),
            "max_iterations": state.get("max_iterations", 3),
            "feedback_len": len(feedback),
            "feedback_preview": truncate_text(feedback, max_len=80),
        },
    )

    if state.get("iteration_count", 0) >= state.get("max_iterations", 3):
        logger.warning(
            "router_early_exit",
            extra={
                "event": "router.early_exit",
                "run_id": run_id,
                "reason": "max_iterations_reached",
            },
        )
        return {
            "routing_decision": RoutingAction.DISCARD,
            "discard_reason": "max_iterations reached",
        }

    if not feedback:
        logger.warning(
            "router_early_exit",
            extra={
                "event": "router.early_exit",
                "run_id": run_id,
                "reason": "no_feedback",
            },
        )
        return {
            "routing_decision": RoutingAction.DISCARD,
            "discard_reason": "No manager feedback provided",
            "iteration_count": state.get("iteration_count", 0) + 1,
        }

    lower_feedback = feedback.lower()
    if lower_feedback in {"approve", "publish", "approved", "looks good"}:
        logger.info(
            "router_decision_complete",
            extra={
                "event": "router.decision_complete",
                "run_id": run_id,
                "routing_decision": RoutingAction.PUBLISH,
                "iteration_count": state.get("iteration_count", 0) + 1,
            },
        )
        return {
            "routing_decision": RoutingAction.PUBLISH,
            "iteration_count": state.get("iteration_count", 0) + 1,
            "rewrite_instructions": None,
        }

    try:
        start_ts = time.perf_counter()
        llm = _build_router_llm().with_structured_output(RoutingDecision)
        decision_raw = llm.invoke(
            [
                SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                HumanMessage(content=feedback),
            ]
        )
        decision = _coerce_routing_decision(decision_raw)
        logger.info(
            "router_llm_classification",
            extra={
                "event": "router.llm_classification",
                "run_id": run_id,
                "routing_decision": decision.action,
                "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            },
        )

        rewrite_instructions = (
            feedback if decision.action == RoutingAction.REWRITE else None
        )
        logger.info(
            "router_decision_complete",
            extra={
                "event": "router.decision_complete",
                "run_id": run_id,
                "routing_decision": decision.action,
                "iteration_count": state.get("iteration_count", 0) + 1,
            },
        )
        return {
            "routing_decision": decision.action,
            "rewrite_instructions": rewrite_instructions,
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "router_error",
            extra={
                "event": "router.error",
                "run_id": run_id,
                **sanitize_error(exc),
            },
        )
        return {
            "routing_decision": RoutingAction.DISCARD,
            "discard_reason": f"Router classification failed: {exc}",
            "iteration_count": state.get("iteration_count", 0) + 1,
        }


def augment_dispatch_node(state: AgentState) -> dict:
    return {}


def publish_node(state: AgentState) -> dict:
    logger.info(
        "publish",
        extra={
            "event": "publish.complete",
            "run_id": state.get("run_id", "unknown"),
            "digest_len": len(state.get("digest") or ""),
        },
    )
    print("\n=== PUBLISH (stdout stub) ===")
    print(state.get("digest", ""))
    print("=== END PUBLISH ===\n")
    return {"published": True}


def discard_node(state: AgentState) -> dict:
    reason = state.get("discard_reason") or "Discarded by manager"
    logger.info(
        "discard",
        extra={
            "event": "discard.complete",
            "run_id": state.get("run_id", "unknown"),
            "reason": truncate_text(reason, max_len=160),
        },
    )
    print(f"\n[DISCARD] {reason}\n")
    return {"published": False}
