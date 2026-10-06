from __future__ import annotations

import asyncio
import os
import signal
import sys

from course_discovery.chat.handlers import chat_handlers
from course_discovery.chat.runner import GraphRunner
from course_discovery.chat.store import ChatStore
from course_discovery.chat.transport import TelegramTransport
from course_discovery.chat.worker import ChatWorker
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.handlers import SEND_DIGEST_MESSAGE
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.review import nodes as review_nodes
from course_discovery.workflows.outer_graph import build_graph


class DieAfterDigest:
    def __init__(self, gateway):
        self.gateway = gateway

    def submit(self, effect):
        status = self.gateway.submit(effect)
        if effect.kind == SEND_DIGEST_MESSAGE:
            os.kill(os.getpid(), signal.SIGKILL)
        return status

    def status(self, key):
        return self.gateway.status(key)


async def main(api_base: str) -> None:
    url = os.environ["DATABASE_URL"]
    store = ChatStore(url, lease_seconds=1.0)
    outbox = PostgresOutboxStore(url)
    gateway = InlineGateway(outbox, OutboxWorker(outbox, chat_handlers(store, TelegramTransport("t", api_base))))
    set_gateway(gateway)
    review_nodes.get_gateway = lambda: DieAfterDigest(gateway)
    async with open_checkpointer(url) as saver:
        await ChatWorker(store, GraphRunner(build_graph(checkpointer=saver))).run_once()


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
