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
from test_integration_postgres import QUERY, _durable_saver, _thread

from course_discovery.app.cli import _initial_state
from course_discovery.effects.factory import set_gateway
from course_discovery.workflows import research_graph as research_module
from course_discovery.workflows.outer_graph import build_graph

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
ROOT = Path(__file__).parent.parent
CHILD = Path(__file__).parent / "kill_child.py"


def _run_child_until_killed(mode: str, thread_id: str) -> None:
    env = {**os.environ, "DATABASE_URL": TEST_DATABASE_URL, "PYTHONPATH": str(ROOT)}
    env.pop("OPENROUTER_API_KEY", None)
    proc = subprocess.run(
        [sys.executable, str(CHILD), mode, thread_id],
        cwd=ROOT,
        env=env,
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == -signal.SIGKILL, proc.stderr.decode()


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class ProcessKillTests(unittest.IsolatedAsyncioTestCase):
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

    def _count_validator_calls(self):
        calls = []
        real = research_module.evidence_validator_node

        def validator(state):
            calls.append(1)
            return real(state)

        patcher = patch.object(research_module, "evidence_validator_node", validator)
        patcher.start()
        self.addCleanup(patcher.stop)
        return calls

    async def test_sync_mode_resumes_after_sigkill_without_rerunning_validator(self):
        thread_id = f"run-kill-sync-{uuid.uuid4().hex[:8]}"
        _run_child_until_killed("sync", thread_id)
        calls = self._count_validator_calls()

        async with _durable_saver() as saver:
            graph = build_graph(checkpointer=saver)
            await graph.ainvoke(None, _thread(thread_id))
            state = await graph.aget_state(_thread(thread_id))

        self.assertEqual(state.next, ("await_human_review",))
        self.assertEqual(calls, [])

    async def test_exit_mode_loses_the_run_on_sigkill_and_restarts_from_scratch(self):
        thread_id = f"run-kill-exit-{uuid.uuid4().hex[:8]}"
        _run_child_until_killed("exit", thread_id)
        calls = self._count_validator_calls()

        async with _durable_saver() as saver:
            await saver.setup()
            self.assertEqual([c async for c in saver.alist(_thread(thread_id))], [])
            graph = build_graph(checkpointer=saver)
            await graph.ainvoke(_initial_state(QUERY), _thread(thread_id))
            state = await graph.aget_state(_thread(thread_id))

        self.assertEqual(state.next, ("await_human_review",))
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
