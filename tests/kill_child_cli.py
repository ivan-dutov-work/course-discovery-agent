from __future__ import annotations

import asyncio
import os
import signal
import sys

from langchain_core.runnables import RunnableConfig

from course_discovery.app.cli import _initial_state, _stream_until_pause
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.research_agent.cache import nodes as cache_nodes
from course_discovery.workflows.outer_graph import build_graph

QUERY = "Find free Python courses with certificate for beginners"


class _LibraryDefault:
    def __init__(self, graph):
        self._graph = graph

    def __getattr__(self, name):
        return getattr(self._graph, name)

    def astream(self, *args, durability=None, **kwargs):
        return self._graph.astream(*args, **kwargs)


def _die(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)


async def main(variant: str, thread_id: str) -> None:
    cache_nodes.stage_courses = _die
    config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
    async with open_checkpointer() as saver:
        graph = build_graph(checkpointer=saver)
        if variant == "library_default":
            graph = _LibraryDefault(graph)
        await _stream_until_pause(graph, _initial_state(QUERY), config, resume=False)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
