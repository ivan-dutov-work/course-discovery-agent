from __future__ import annotations

import time
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.rate_limiters import InMemoryRateLimiter
from langchain_core.runnables import RunnableConfig

from course_discovery.app.llm import build_llm, llm_enabled
from course_discovery.app.prompts import ROUTER_SYSTEM_PROMPT
from course_discovery.domain.models import RoutingAction, RoutingDecision
from course_discovery.domain.run_config import current_run_id, max_review_rounds, run_id_of
from course_discovery.domain.state import AgentState
from course_discovery.effects.factory import get_gateway
from course_discovery.effects.handlers import PUBLISH_DIGEST
from course_discovery.effects.models import Effect
from course_discovery.guardrails import redact_pii
from course_discovery.observability.logging import (
    get_logger,
    sanitize_error,
    preview,
)
from course_discovery.observability.metrics import record_degradation, record_review_decision


logger = get_logger(__name__)

_router_rate_limiter = InMemoryRateLimiter(requests_per_second=2, max_bucket_size=4)

APPROVAL_PHRASES = frozenset({"approve", "publish", "approved", "looks good"})


def _coerce_routing_decision(value: Any) -> RoutingDecision:
    if isinstance(value, RoutingDecision):
        return value
    if isinstance(value, dict):
        return RoutingDecision.model_validate(value)
    return RoutingDecision.model_validate(getattr(value, "model_dump", lambda: value)())


def router_node(state: AgentState, config: RunnableConfig) -> dict:
    update = _route(state, config)
    update["manager_feedback"] = None
    feedback = redact_pii((state.get("manager_feedback") or "").strip())
    if feedback:
        update["feedback_history"] = [feedback]
    decision = update.get("routing_decision")
    if decision is not None:
        record_review_decision(RoutingAction(decision).value)
    return update


def _route(state: AgentState, config: RunnableConfig) -> dict:
    run_id = run_id_of(config)
    review_round = len(state.get("feedback_history") or []) + 1
    feedback = (state.get("manager_feedback") or "").strip()
    logger.info(
        "router_decision_start",
        extra={
            "event": "router.decision_start",
            "run_id": run_id,
            "review_round": review_round,
            "max_review_rounds": max_review_rounds(config),
            "feedback_len": len(feedback),
            "feedback_preview": preview(feedback, max_len=80),
        },
    )

    if review_round > max_review_rounds(config):
        record_degradation("router", "max_iterations_reached")
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
        record_degradation("router", "no_feedback")
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
        }

    lower_feedback = feedback.lower()
    if lower_feedback in APPROVAL_PHRASES:
        logger.info(
            "router_decision_complete",
            extra={
                "event": "router.decision_complete",
                "run_id": run_id,
                "routing_decision": RoutingAction.PUBLISH,
                "review_round": review_round,
            },
        )
        return {
            "routing_decision": RoutingAction.PUBLISH,
            "rewrite_instructions": None,
        }
    if not llm_enabled():
        if lower_feedback.startswith("rewrite"):
            action = RoutingAction.REWRITE
            rewrite_instructions = feedback
        elif lower_feedback.startswith("augment") or "more" in lower_feedback:
            action = RoutingAction.AUGMENT
            rewrite_instructions = None
        elif lower_feedback.startswith("reset"):
            action = RoutingAction.RESET
            rewrite_instructions = None
        else:
            action = RoutingAction.DISCARD
            rewrite_instructions = None
        return {
            "routing_decision": action,
            "rewrite_instructions": rewrite_instructions,
            "discard_reason": "Discarded by manager" if action == RoutingAction.DISCARD else None,
        }

    try:
        start_ts = time.perf_counter()
        llm = build_llm("router", rate_limiter=_router_rate_limiter, max_retries=2).with_structured_output(RoutingDecision)
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
                "review_round": review_round,
            },
        )
        return {
            "routing_decision": decision.action,
            "rewrite_instructions": rewrite_instructions,
        }
    except Exception as exc:  # noqa: BLE001
        record_degradation("router", "classification_failed")
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
        }


def publish_node(state: AgentState, config: RunnableConfig) -> dict:
    run_id = run_id_of(config)
    effect = Effect(
        key=f"publish:{run_id}",
        kind=PUBLISH_DIGEST,
        payload={
            "run_id": run_id,
            "user_id": state.get("user_id"),
            "query": state.get("user_query"),
            "digest": state.get("digest") or "",
            "course_urls": [course.url for course in state.get("valid_courses", [])],
        },
    )
    status = get_gateway().submit(effect)
    logger.info(
        "publish",
        extra={
            "event": "publish.submitted",
            "run_id": run_id,
            "effect_key": effect.key,
            "publish_status": status,
            "digest_len": len(state.get("digest") or ""),
        },
    )
    return {"publish_status": status}


def discard_node(state: AgentState) -> dict:
    reason = state.get("discard_reason") or "Discarded by manager"
    logger.info(
        "discard",
        extra={
            "event": "discard.complete",
            "run_id": current_run_id(),
            "reason": preview(reason, max_len=160),
        },
    )
    print(f"\n[DISCARD] {reason}\n")
    return {}
