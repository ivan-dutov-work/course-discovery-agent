from __future__ import annotations

from course_discovery.domain.models import ResearchRunMetrics, UserMemory
from course_discovery.domain.run_config import current_run_id
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger
from course_discovery.observability.metrics import record_cache_lookup
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

    candidates = search_course_cache(
        filters, state.get("user_memory") or UserMemory(), user_id=state.get("user_id")
    )
    metrics = (state.get("metrics") or ResearchRunMetrics()).model_copy(
        update={"cache_hits": len(candidates)}
    )
    record_cache_lookup(len(candidates))
    logger.info(
        "course_cache_lookup_complete",
        extra={
            "event": "cache.lookup_complete",
            "run_id": current_run_id(),
            "candidate_count": len(candidates),
        },
    )
    return {
        "cache_candidates": candidates,
        "metrics": metrics,
    }


def course_cache_upsert_node(state: AgentState) -> dict:
    useful_courses = state.get("valid_courses", []) + state.get("uncertain_courses", [])
    upsert_courses(useful_courses, state.get("validation_results", []))
    logger.info(
        "course_cache_upsert_complete",
        extra={
            "event": "cache.upsert_complete",
            "run_id": current_run_id(),
            "course_count": len(useful_courses),
        },
    )
    return {}
