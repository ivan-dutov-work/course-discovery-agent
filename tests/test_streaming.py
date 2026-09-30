from __future__ import annotations

import contextlib
import io
import os
import unittest
from unittest.mock import patch

from langgraph.errors import GraphRecursionError

from course_discovery.app.cli import _initial_state, _stream_until_pause
from course_discovery.workflows.outer_graph import build_graph

QUERY = "Find free Python courses with certificate for beginners"


class StreamingRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        os.environ.pop("DATABASE_URL", None)

    async def test_progress_streams_from_inside_the_subgraph_and_stops_at_review(self):
        graph = build_graph()
        config = {"configurable": {"thread_id": "stream-1"}}
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            state = await _stream_until_pause(
                graph, _initial_state(QUERY, "stream-1"), config, resume=False
            )

        lines = out.getvalue().splitlines()
        self.assertIn("- parse_user_request", lines)
        self.assertIn("  - search_web_for_courses", lines)
        self.assertTrue(any("searched '" in line for line in lines))
        self.assertLess(lines.index("- parse_user_request"), lines.index("  - rank_and_summarize_courses"))
        self.assertEqual((await graph.aget_state(config)).next, ("await_human_review",))
        self.assertTrue(state["digest"])

    async def test_recursion_limit_stops_a_runaway_graph(self):
        graph = build_graph()
        config = {"configurable": {"thread_id": "stream-2"}, "recursion_limit": 3}

        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(GraphRecursionError):
            await _stream_until_pause(
                graph, _initial_state(QUERY, "stream-2"), config, resume=False
            )


if __name__ == "__main__":
    unittest.main()
