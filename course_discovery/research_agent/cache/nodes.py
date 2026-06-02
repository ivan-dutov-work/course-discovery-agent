from __future__ import annotations

from course_discovery.domain.models import ResearchRunMetrics, UserMemory
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger
from course_discovery.research_agent.cache.repository import (
    search_course_cache,
    upsert_courses,
)


logger = get_logger(__name__)


def course_cache_lookup_node(state: AgentState) -> dict:
    filters = state.get("search_filters")
    if filters is None:
        return {
            "cache_candidates": [],
            "research_notes": ["Cache lookup skipped because filters were unavailable."],
        }

    candidates = search_course_cache(filters, state.get("user_memory") or UserMemory())
    metrics = (state.get("metrics") or ResearchRunMetrics()).model_copy(
        update={"cache_hits": len(candidates)}
    )
    logger.info(
        "course_cache_lookup_complete",
        extra={
            "event": "cache.lookup_complete",
            "run_id": state.get("run_id", "unknown"),
            "candidate_count": len(candidates),
        },
    )
    return {
        "cache_candidates": candidates,
        "cache_hits": len(candidates),
        "metrics": metrics,
    }


def course_cache_upsert_node(state: AgentState) -> dict:
    useful_courses = state.get("valid_courses", []) + state.get("uncertain_courses", [])
    upsert_courses(useful_courses, state.get("validation_results", []))
    logger.info(
        "course_cache_upsert_complete",
        extra={
            "event": "cache.upsert_complete",
            "run_id": state.get("run_id", "unknown"),
            "course_count": len(useful_courses),
        },
    )
    return {}
