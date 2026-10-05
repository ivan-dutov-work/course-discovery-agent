from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from course_discovery.domain.models import ResearchRunMetrics, UserMemory
from course_discovery.domain.run_config import current_run_id, run_id_of
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger
from course_discovery.observability.metrics import record_cache_lookup
from course_discovery.research_agent.cache.repository import (
    discard_staged_courses,
    promote_staged_courses,
    search_course_cache,
    stage_courses,
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
    staged = [course for course in state.get("valid_courses", []) if course.source != "cache"]
    stage_courses(current_run_id(), staged, state.get("validation_results", []))
    logger.info(
        "course_cache_stage_complete",
        extra={
            "event": "cache.stage_complete",
            "run_id": current_run_id(),
            "course_count": len(staged),
        },
    )
    return {}


def promote_approved_courses_node(state: AgentState, config: RunnableConfig) -> dict:
    run_id = run_id_of(config)
    approved = [course.url for course in state.get("valid_courses", []) if course.source != "cache"]
    promoted = promote_staged_courses(run_id, approved)
    logger.info(
        "course_cache_promote_complete",
        extra={"event": "cache.promote_complete", "run_id": run_id, "promoted": promoted},
    )
    return {}


def drop_pending_courses_node(state: AgentState, config: RunnableConfig) -> dict:
    run_id = run_id_of(config)
    discard_staged_courses(run_id)
    logger.info(
        "course_cache_drop_complete",
        extra={"event": "cache.drop_complete", "run_id": run_id},
    )
    return {}
