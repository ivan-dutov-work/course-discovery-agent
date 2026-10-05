from __future__ import annotations

RESEARCH_NS = "course_research"


async def research_values(graph, thread_id: str) -> dict:
    found = await graph.checkpointer.aget_tuple(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": RESEARCH_NS}}
    )
    return dict(found.checkpoint["channel_values"]) if found else {}
