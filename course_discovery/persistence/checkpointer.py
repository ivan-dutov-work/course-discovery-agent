from __future__ import annotations

import inspect
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import AsyncIterator

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import BaseModel

from course_discovery.domain import models as domain_models


def msgpack_allowlist() -> list[tuple[str, str]]:
    return [
        (cls.__module__, cls.__name__)
        for _, cls in inspect.getmembers(domain_models, inspect.isclass)
        if issubclass(cls, (BaseModel, Enum)) and cls.__module__ == domain_models.__name__
    ]


def build_serde() -> JsonPlusSerializer:
    return JsonPlusSerializer(allowed_msgpack_modules=msgpack_allowlist())


def memory_saver() -> MemorySaver:
    return MemorySaver(serde=build_serde())


@asynccontextmanager
async def open_checkpointer(database_url: str | None = None) -> AsyncIterator[BaseCheckpointSaver]:
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        yield memory_saver()
        return
    async with AsyncPostgresSaver.from_conn_string(url, serde=build_serde()) as saver:
        await saver.setup()
        yield saver


async def prune_checkpoints(
    saver: AsyncPostgresSaver, older_than_days: float, now: datetime | None = None
) -> list[str]:
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
    async with saver._cursor() as cur:
        await cur.execute(
            """
            SELECT thread_id FROM checkpoints
            GROUP BY thread_id
            HAVING max((checkpoint ->> 'ts')::timestamptz) < %s
            """,
            (cutoff,),
        )
        thread_ids = [row["thread_id"] for row in await cur.fetchall()]
    for thread_id in thread_ids:
        await saver.adelete_thread(thread_id)
    return thread_ids
