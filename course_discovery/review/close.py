from __future__ import annotations

from course_discovery.domain.models import RoutingAction
from course_discovery.privacy import authorize_thread

IMPLICIT = "implicit"


async def close_thread(graph, user_id: str, thread_id: str, *, reason: str = IMPLICIT) -> bool:
    authorize_thread(user_id, thread_id)
    config = {
        "configurable": {"thread_id": thread_id, "chat_mode": True, "close_reason": reason},
        "recursion_limit": 60,
    }
    snapshot = await graph.aget_state(config)
    if snapshot.next != ("await_human_review",):
        return False
    await graph.aupdate_state(
        config,
        {"routing_decision": RoutingAction.PUBLISH},
        as_node="interpret_review_feedback",
    )
    async for _ in graph.astream(None, config, stream_mode="updates", durability="sync"):
        pass
    return True
