from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import urllib.request
import uuid

from course_discovery.chat.handlers import chat_handlers
from course_discovery.chat.runner import GraphRunner
from course_discovery.chat.store import ChatStore
from course_discovery.chat.transport import TelegramTransport
from course_discovery.chat.webhook import ChatIntake, make_server, serve_in_thread
from course_discovery.chat.worker import ChatWorker
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.workflows.outer_graph import build_graph

QUERY = "Find free Python courses with certificate for beginners"


def check(condition: bool, label: str) -> None:
    print(("ok   " if condition else "FAIL ") + label)
    if not condition:
        sys.exit(1)


def post(port: int, payload: dict) -> None:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/webhook",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    urllib.request.urlopen(request, timeout=10).read()


async def main() -> None:
    url = os.environ.get("TEST_DATABASE_URL") or os.environ["DATABASE_URL"]
    os.environ["DATABASE_URL"] = url
    os.environ.pop("OPENROUTER_API_KEY", None)
    api = subprocess.Popen([sys.executable, "-m", "course_discovery.chat.fake_bot_api"], stdout=subprocess.PIPE, text=True)
    try:
        api_base = f"http://127.0.0.1:{int(api.stdout.readline())}"
        transport = TelegramTransport("smoke", api_base)
        store = ChatStore(url)
        outbox = PostgresOutboxStore(url)
        set_gateway(InlineGateway(outbox, OutboxWorker(outbox, chat_handlers(store, transport))))
        server = make_server(ChatIntake(store, transport))
        serve_in_thread(server)
        port = server.server_address[1]

        chat_id = uuid.uuid4().int % 10**9
        update_id = chat_id * 100
        body = {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": QUERY}}
        for _ in range(3):
            post(port, body)
        post(port, {"update_id": update_id + 1, "message": {"chat": {"id": chat_id}, "text": "under 20 hours"}})

        async with open_checkpointer(url) as saver:
            graph = build_graph(checkpointer=saver)
            workers = [ChatWorker(store, GraphRunner(graph)) for _ in range(2)]
            ran = await asyncio.gather(*(worker.run_once() for worker in workers))
            while any(await asyncio.gather(*(worker.run_once() for worker in workers))):
                pass
        check(sum(ran) == 1, "two workers, one user: exactly one claimed the batch")

        sent = json.loads(urllib.request.urlopen(f"{api_base}/sent").read())
        check(len(sent) == 2, "reply captured: one digest and one feedback prompt")
        check(sent[0]["chat_id"] == chat_id and sent[0]["text"], "digest addressed to the sender")
        server.shutdown()
        print("chat smoke passed")
    finally:
        api.kill()
        api.wait()
        api.stdout.close()


if __name__ == "__main__":
    asyncio.run(main())
