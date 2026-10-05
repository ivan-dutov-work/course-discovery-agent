from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from course_discovery.domain.models import DeliveryStatus
from course_discovery.domain.run_config import current_run_id, run_id_of
from course_discovery.domain.state import AgentState, ResearchState
from course_discovery.observability.logging import get_logger
from course_discovery.research_agent.memory.repository import (
    load_user_memory,
    record_feedback,
)


logger = get_logger(__name__)


def user_memory_lookup_node(state: ResearchState) -> dict:
    memory = load_user_memory(state.get("user_id"))
    logger.info(
        "user_memory_lookup_complete",
        extra={
            "event": "memory.lookup_complete",
            "run_id": current_run_id(),
            "preferred_provider_count": len(memory.preferred_providers),
            "avoided_provider_count": len(memory.avoided_providers),
            "completed_count": len(memory.completed_course_urls),
            "rejected_count": len(memory.rejected_course_urls),
        },
    )
    return {"user_memory": memory}


def user_memory_update_node(state: AgentState, config: RunnableConfig) -> dict:
    run_id = run_id_of(config)
    feedback = "\n".join(state.get("feedback_history") or []) or None
    accepted = state.get("publish_status") in {DeliveryStatus.QUEUED, DeliveryStatus.DELIVERED}
    record_feedback(
        state.get("user_id"),
        state.get("valid_courses", []),
        state.get("user_query", ""),
        accepted=accepted,
        feedback_text=feedback,
        run_id=run_id,
    )
    logger.info(
        "user_memory_update_complete",
        extra={
            "event": "memory.update_complete",
            "run_id": run_id,
            "accepted": accepted,
            "course_count": len(state.get("valid_courses", [])),
        },
    )
    return {}
