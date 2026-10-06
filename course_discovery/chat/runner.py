from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from course_discovery.app.cli import _initial_state
from course_discovery.privacy import register_thread
from course_discovery.resilience import RECURSION_LIMIT


class GraphRunner:
    def __init__(self, graph) -> None:
        self.graph = graph

    async def __call__(self, user_id: str, thread_id: str, text: str) -> None:
        config: RunnableConfig = {
            "configurable": {"thread_id": thread_id, "chat_mode": True},
            "recursion_limit": RECURSION_LIMIT,
        }
        snapshot = await self.graph.aget_state(config)
        if snapshot.values:
            if snapshot.next == ("await_human_review",) or not snapshot.next:
                return
            inputs = None
        else:
            register_thread(user_id, thread_id)
            inputs = {**_initial_state(text), "user_id": user_id}
        async for _ in self.graph.astream(
            inputs, config, stream_mode="updates", durability="sync"
        ):
            pass
