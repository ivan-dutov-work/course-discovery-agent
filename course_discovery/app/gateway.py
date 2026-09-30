from __future__ import annotations

import time
from typing import cast

from langchain_core.messages import HumanMessage, SystemMessage
from course_discovery.app.llm import build_llm, llm_enabled
from course_discovery.app.prompts import GATEWAY_SYSTEM_PROMPT
from course_discovery.domain.models import RoutingAction, SearchFilters
from course_discovery.domain.state import AgentState
from course_discovery.guardrails import redact_pii
from course_discovery.observability.logging import (
    get_logger,
    sanitize_error,
    preview,
)
from course_discovery.observability.metrics import record_degradation
from course_discovery.research_agent.memory.defaults import apply_profile_defaults
from course_discovery.research_agent.memory.repository import load_user_memory
from course_discovery.resilience import is_transient


logger = get_logger(__name__)


def _fallback_parse_filters(query: str) -> SearchFilters:
    lower = query.lower()
    level = "any"
    for candidate in ["beginner", "intermediate", "advanced"]:
        if candidate in lower:
            level = candidate
            break
    topic = query
    for marker in ["courses", "course", "with", "for"]:
        topic = topic.replace(marker, " ")
    return SearchFilters(
        topic=" ".join(topic.split()) or "general programming",
        max_price=0.0 if "free" in lower else 999.0,
        include_certificate="certificate" in lower or "certification" in lower,
        level=level,
        content_languages=["en"],
    )


def _parse_filters(query: str) -> SearchFilters:
    if not llm_enabled():
        return _fallback_parse_filters(query)
    llm = build_llm("gateway").with_structured_output(SearchFilters)
    result = llm.invoke(
        [
            SystemMessage(content=GATEWAY_SYSTEM_PROMPT),
            HumanMessage(content=query),
        ]
    )
    return cast(SearchFilters, result)


def gateway_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    run_id = state.get("run_id", "unknown")

    try:
        query_for_parsing = redact_pii(state["user_query"]) or ""
        logger.info(
            "gateway_parse_start",
            extra={
                "event": "gateway.parse_start",
                "run_id": run_id,
                "query_preview": preview(query_for_parsing, max_len=100),
                "query_len": len(query_for_parsing),
                "is_reset": state.get("routing_decision") == RoutingAction.RESET,
                "has_existing_filters": state.get("search_filters") is not None,
            },
        )

        if state.get("routing_decision") == RoutingAction.RESET and state.get(
            "manager_feedback"
        ):
            query_for_parsing = (
                f"{query_for_parsing}\n\nReset overrides: "
                f"{redact_pii(state['manager_feedback'])}"
            )

        parsed_filters = apply_profile_defaults(
            _parse_filters(query_for_parsing),
            query_for_parsing,
            load_user_memory(state.get("user_id")),
        )
        logger.info(
            "gateway_filters_parsed",
            extra={
                "event": "gateway.filters_parsed",
                "run_id": run_id,
                "topic_preview": preview(parsed_filters.topic, max_len=60),
                "max_price": parsed_filters.max_price,
                "min_rating": parsed_filters.min_rating,
                "include_certificate": parsed_filters.include_certificate,
                "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            },
        )

        existing_filters = state.get("search_filters")
        if existing_filters and state.get("routing_decision") == RoutingAction.RESET:
            old_filters = existing_filters.model_dump()
            new_filters = parsed_filters.model_dump()
            fields_changed = sorted(
                key
                for key in new_filters
                if old_filters.get(key) != new_filters.get(key)
            )

            merged = existing_filters.model_copy(
                update=parsed_filters.model_dump(exclude_unset=True)
            )
            parsed_filters = merged
            logger.info(
                "gateway_merge_filters",
                extra={
                    "event": "gateway.merge_filters",
                    "run_id": run_id,
                    "fields_changed": fields_changed,
                },
            )

        return {
            "search_filters": parsed_filters,
            "routing_decision": None,
            "manager_feedback": None,
            "error": None,
        }
    except Exception as exc:  # noqa: BLE001
        if is_transient(exc):
            raise
        err = sanitize_error(exc)
        record_degradation("gateway", "parse_failed")
        logger.error(
            "gateway_error",
            extra={
                "event": "gateway.error",
                "run_id": run_id,
                "duration_ms": int((time.perf_counter() - start_ts) * 1000),
                **err,
            },
        )
        return {
            "error": f"Gateway parsing failed: {exc}",
            "routing_decision": RoutingAction.DISCARD,
            "discard_reason": f"Gateway failed ({err['error_type']}). Check logs.",
        }
