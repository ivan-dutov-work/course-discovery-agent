from __future__ import annotations

from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger
from course_discovery.research_agent.memory.repository import (
    load_user_memory,
    record_feedback,
)


logger = get_logger(__name__)


def user_memory_lookup_node(state: AgentState) -> dict:
    memory = load_user_memory(state.get("user_id"))
    logger.info(
        "user_memory_lookup_complete",
        extra={
            "event": "memory.lookup_complete",
            "run_id": state.get("run_id", "unknown"),
            "preferred_provider_count": len(memory.preferred_providers),
            "avoided_provider_count": len(memory.avoided_providers),
            "completed_count": len(memory.completed_course_urls),
            "rejected_count": len(memory.rejected_course_urls),
        },
    )
    return {"user_memory": memory}


def user_memory_update_node(state: AgentState) -> dict:
    feedback = state.get("manager_feedback")
    accepted = bool(state.get("published"))
    record_feedback(
        state.get("user_id"),
        state.get("valid_courses", []),
        state.get("user_query", ""),
        accepted=accepted,
        feedback_text=feedback,
    )
    logger.info(
        "user_memory_update_complete",
        extra={
            "event": "memory.update_complete",
            "run_id": state.get("run_id", "unknown"),
            "accepted": accepted,
            "course_count": len(state.get("valid_courses", [])),
        },
    )
    return {}
