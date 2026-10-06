from __future__ import annotations

import os
import signal
import subprocess
import sys
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg
from langgraph.checkpoint.memory import MemorySaver
from test_integration_postgres import _durable_saver, _thread

from course_discovery.app import cli
from course_discovery.effects.factory import set_gateway
from course_discovery.workflows import research_graph as research_module
from course_discovery.workflows.outer_graph import build_graph

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ROOT = Path(__file__).parent.parent
CHILD = Path(__file__).parent / "kill_child_cli.py"


class _Spy:
    def __init__(self, graph):
        self._graph = graph
        self.calls: list[tuple[str, dict]] = []

    def __getattr__(self, name):
        attr = getattr(self._graph, name)
        if name not in ("ainvoke", "astream", "invoke", "stream"):
            return attr

        def recorded(*args, **kwargs):
            self.calls.append((name, kwargs))
            return attr(*args, **kwargs)

        return recorded


class CliDurabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DATABASE_URL": ""})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        set_gateway(None)
        self.addCleanup(set_gateway, None)

    async def test_every_graph_invocation_passes_durability_sync(self):
        spy = _Spy(build_graph(checkpointer=MemorySaver()))
        answers = iter(["", "approve"])
        with patch("builtins.input", lambda *_: next(answers)):
            await cli._run(spy)

        self.assertGreaterEqual(len(spy.calls), 2)
        for index, (method, kwargs) in enumerate(spy.calls):
            self.assertEqual(
                kwargs.get("durability"),
                "sync",
                f"{method} call {index} did not pass durability='sync'",
            )


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class CliKillTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        set_gateway(None)
        self.addCleanup(set_gateway, None)
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
            conn.execute(
                "TRUNCATE recommendation_events, course_evidence, courses, pending_courses, users CASCADE"
            )

    def _kill_child(self, variant: str, thread_id: str) -> None:
        env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": str(ROOT)}
        proc = subprocess.run(
            [sys.executable, str(CHILD), variant, thread_id],
            cwd=ROOT,
            env=env,
            capture_output=True,
            timeout=60,
        )
        self.assertEqual(proc.returncode, -signal.SIGKILL, proc.stderr.decode())

    async def _resume(self, thread_id: str):
        calls = []
        real = research_module.evidence_validator_node

        def validator(state):
            calls.append(1)
            return real(state)

        with patch.object(research_module, "evidence_validator_node", validator):
            async with _durable_saver() as saver:
                graph = build_graph(checkpointer=saver)
                await cli._stream_until_pause(
                    graph, None, _thread(thread_id), resume=True
                )
                state = await graph.aget_state(_thread(thread_id))
        return state, calls

    async def test_cli_path_resumes_after_sigkill_without_rerunning_validator(self):
        thread_id = f"cli-kill-{uuid.uuid4().hex[:8]}"
        self._kill_child("shipped", thread_id)

        state, calls = await self._resume(thread_id)

        self.assertEqual(state.next, ("await_human_review",))
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
