from __future__ import annotations

import asyncio
import os

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


async def serve() -> None:
    url = os.environ["DATABASE_URL"]
    transport = TelegramTransport(
        os.environ["TELEGRAM_BOT_TOKEN"], os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")
    )
    store = ChatStore(url)
    outbox = PostgresOutboxStore(url)
    set_gateway(InlineGateway(outbox, OutboxWorker(outbox, chat_handlers(store, transport))))
    server = make_server(
        ChatIntake(store, transport),
        host=os.getenv("CHAT_HOST", "0.0.0.0"),
        port=int(os.getenv("CHAT_PORT", "8080")),
        shared_key=os.getenv("TELEGRAM_WEBHOOK_SECRET"),
    )
    serve_in_thread(server)
    async with open_checkpointer(url) as saver:
        worker = ChatWorker(store, GraphRunner(build_graph(checkpointer=saver)))
        await worker.run_until(asyncio.Event())


if __name__ == "__main__":
    asyncio.run(serve())
