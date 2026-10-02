from __future__ import annotations

import asyncio
import os
import signal
import sys

from course_discovery.app.cli import _initial_state
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.research_agent.cache import nodes as cache_nodes
from course_discovery.workflows.outer_graph import build_graph

QUERY = "Find free Python courses with certificate for beginners"


def _die(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)


async def main(mode: str, thread_id: str) -> None:
    cache_nodes.upsert_courses = _die
    async with open_checkpointer() as saver:
        graph = build_graph(checkpointer=saver)
        await graph.ainvoke(
            _initial_state(QUERY),
            {"configurable": {"thread_id": thread_id}},
            durability=mode,
        )


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
