from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import unittest
import urllib.request
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg

from course_discovery.chat import store as chat_store
from course_discovery.chat.handlers import chat_handlers
from course_discovery.chat.runner import GraphRunner
from course_discovery.chat.store import ChatStore
from course_discovery.chat.transport import FakeTransport, TelegramTransport
from course_discovery.chat.webhook import ChatIntake, make_server, serve_in_thread
from course_discovery.chat.worker import ChatWorker
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.privacy import erase_user
from course_discovery.workflows.outer_graph import build_graph

ROOT = Path(__file__).parent.parent
CHILD = Path(__file__).parent / "kill_child_chat.py"
QUERY = "Find free Python courses with certificate for beginners"


def post(port: int, payload: dict) -> int:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhook",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status


def update(update_id: int, chat_id: int, text: str) -> dict:
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class ChatSurfaceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)
        base = uuid.uuid4().int % 10**9 * 100
        self.base = base
        self.chat_id = base
        self.store = ChatStore(self.url, lease_seconds=30)
        self.transport = FakeTransport()
        self.intake = ChatIntake(self.store, self.transport)
        self.server = make_server(self.intake)
        serve_in_thread(self.server)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self._cleanup)
        self.addCleanup(set_gateway, None)

    def _cleanup(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            users = [r[0] for r in conn.execute("SELECT user_id FROM chat_identities WHERE chat_id BETWEEN %s AND %s", (self.base, self.base + 99))]
            conn.execute("DELETE FROM chat_inbox WHERE update_id BETWEEN %s AND %s", (self.base, self.base + 99))
            conn.execute("DELETE FROM chat_updates WHERE update_id BETWEEN %s AND %s", (self.base, self.base + 99))
            for user in users:
                conn.execute("DELETE FROM chat_user_leases WHERE user_id = %s", (user,))
                conn.execute("DELETE FROM outbox WHERE payload ->> 'user_id' = %s", (user,))
                for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                    conn.execute(
                        f"DELETE FROM {table} WHERE thread_id IN (SELECT thread_id FROM run_threads WHERE user_id = %s)",
                        (user,),
                    )
                conn.execute("DELETE FROM pending_courses WHERE run_id IN (SELECT thread_id FROM run_threads WHERE user_id = %s)", (user,))
                conn.execute("DELETE FROM run_threads WHERE user_id = %s", (user,))
                conn.execute("DELETE FROM users WHERE id = %s", (user,))
            conn.execute("DELETE FROM chat_identities WHERE chat_id BETWEEN %s AND %s", (self.base, self.base + 99))

    def _scalar(self, sql: str, *params):
        with psycopg.connect(self.url) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def _inbox(self) -> int:
        return self._scalar("SELECT count(*) FROM chat_inbox WHERE update_id BETWEEN %s AND %s", self.base, self.base + 99)

    async def test_same_update_posted_concurrently_makes_one_inbox_row_and_one_run(self):
        port = self.server.server_address[1]
        results = await asyncio.gather(
            *(asyncio.to_thread(post, port, update(self.base + 1, self.chat_id, QUERY)) for _ in range(5))
        )
        self.assertEqual(results, [200] * 5)
        self.assertEqual(self._inbox(), 1)
        runs: list[tuple] = []

        async def runner(user_id, thread_id, text):
            runs.append((user_id, thread_id, text))

        worker = ChatWorker(self.store, runner)
        while await worker.run_once():
            pass
        self.assertEqual(len(runs), 1)

    async def test_messages_during_a_run_merge_into_one_following_run(self):
        self.intake.handle(update(self.base + 1, self.chat_id, "python courses"))
        runs: list[str] = []
        gate = asyncio.Event()
        started = asyncio.Event()

        async def runner(user_id, thread_id, text):
            runs.append(text)
            started.set()
            if len(runs) == 1:
                await gate.wait()

        worker = ChatWorker(self.store, runner)
        first = asyncio.create_task(worker.run_once())
        await started.wait()
        for i in (2, 3, 4):
            self.intake.handle(update(self.base + i, self.chat_id, f"extra {i}"))
        self.assertFalse(await ChatWorker(self.store, runner).run_once())
        gate.set()
        await first
        while await worker.run_once():
            pass
        self.assertEqual(runs, ["python courses", "extra 2\nextra 3\nextra 4"])

    async def test_two_workers_never_hold_one_user_at_once(self):
        for i in range(1, 7):
            self.intake.handle(update(self.base + i, self.chat_id, f"m{i}"))
            time.sleep(0.01)
        active = 0
        peak = 0
        runs = 0

        async def runner(user_id, thread_id, text):
            nonlocal active, peak, runs
            active += 1
            peak = max(peak, active)
            runs += 1
            await asyncio.sleep(0.2)
            active -= 1

        async def drain(worker):
            while await worker.run_once():
                await asyncio.sleep(0.05)

        await asyncio.gather(*(drain(ChatWorker(self.store, runner)) for _ in range(4)))
        self.assertEqual(peak, 1)
        self.assertEqual(runs, 1)

    async def test_different_users_run_in_parallel(self):
        self.intake.handle(update(self.base + 1, self.chat_id, "a"))
        self.intake.handle(update(self.base + 2, self.chat_id + 1, "b"))
        active = 0
        peak = 0

        async def runner(user_id, thread_id, text):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.3)
            active -= 1

        await asyncio.gather(*(ChatWorker(self.store, runner).run_once() for _ in range(2)))
        self.assertEqual(peak, 2)

    async def test_a_message_with_pii_is_stored_redacted_and_never_reaches_a_table(self):
        email = f"canary{uuid.uuid4().hex[:8]}@example.com"
        card = "4111 1111 1111 1111"
        self.intake.handle(update(self.base + 1, self.chat_id, f"{QUERY}. Write to {email}, card {card}"))
        outbox = PostgresOutboxStore(self.url)
        store = self.store
        set_gateway(InlineGateway(outbox, OutboxWorker(outbox, chat_handlers(store, self.transport))))
        async with open_checkpointer(self.url) as saver:
            worker = ChatWorker(store, GraphRunner(build_graph(checkpointer=saver)))
            self.assertTrue(await worker.run_once())
        self.assertEqual(self._scalar("SELECT count(*) FROM chat_inbox WHERE done_at IS NOT NULL AND update_id = %s", self.base + 1), 1)
        self.assertGreaterEqual(len(self.transport.sent), 1)
        needles = [email, "4111 1111", "4111111111111111", email.encode().hex()]
        with psycopg.connect(self.url) as conn:
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
            ]
            for table in tables:
                for needle in needles:
                    hits = conn.execute(
                        f'SELECT count(*) FROM "{table}" t WHERE t::text LIKE %s', (f"%{needle}%",)
                    ).fetchone()[0]
                    self.assertEqual(hits, 0, f"{needle!r} found in {table}")
        for _, text in self.transport.sent:
            self.assertNotIn(email, text)

    async def test_sigkilled_worker_batch_is_processed_once_and_replied_once(self):
        api = subprocess.Popen(
            [sys.executable, "-m", "course_discovery.chat.fake_bot_api"], stdout=subprocess.PIPE, text=True
        )
        self.addCleanup(api.wait)
        self.addCleanup(api.kill)
        self.addCleanup(api.stdout.close)
        api_base = f"http://127.0.0.1:{int(api.stdout.readline())}"
        self.intake.handle(update(self.base + 1, self.chat_id, QUERY))
        child = subprocess.run(
            [sys.executable, str(CHILD), api_base],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(child.returncode, -9, child.stderr[-500:])
        self.assertEqual(self._inbox_done(), 0)
        sent = json.loads(urllib.request.urlopen(f"{api_base}/sent").read())
        self.assertEqual(len(sent), 1)

        time.sleep(1.2)
        outbox = PostgresOutboxStore(self.url)
        set_gateway(InlineGateway(outbox, OutboxWorker(outbox, chat_handlers(self.store, TelegramTransport("t", api_base)))))
        async with open_checkpointer(self.url) as saver:
            worker = ChatWorker(ChatStore(self.url, lease_seconds=30), GraphRunner(build_graph(checkpointer=saver)))
            self.assertTrue(await worker.run_once())
        self.assertEqual(self._inbox_done(), 1)
        sent = json.loads(urllib.request.urlopen(f"{api_base}/sent").read())
        digests = [m for m in sent if m["text"] != "Tell me what to change, or just ask for something new."]
        self.assertEqual(len(digests), 1)

    def _inbox_done(self) -> int:
        return self._scalar(
            "SELECT count(*) FROM chat_inbox WHERE done_at IS NOT NULL AND update_id BETWEEN %s AND %s",
            self.base,
            self.base + 99,
        )

    async def test_erasure_removes_chat_mapping_and_inbox(self):
        self.intake.handle(update(self.base + 1, self.chat_id, "hello"))
        user = self._scalar("SELECT user_id FROM chat_identities WHERE chat_id = %s", self.chat_id)
        async with open_checkpointer(self.url) as saver:
            dry = await erase_user(user, saver)
            self.assertEqual(dry.rows["chat_inbox"], 1)
            report = await erase_user(user, saver, execute=True)
        self.assertTrue(report.clean)
        self.assertEqual(self._scalar("SELECT count(*) FROM chat_identities WHERE user_id = %s", user), 0)
        self.assertEqual(self._scalar("SELECT count(*) FROM chat_inbox WHERE user_id = %s", user), 0)


if __name__ == "__main__":
    unittest.main()
