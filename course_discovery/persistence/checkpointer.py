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
from langgraph.checkpoint.serde.base import SerializerProtocol
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel

from course_discovery.domain import models as domain_models
from course_discovery.persistence.encryption import (
    AesGcmCipher,
    SealingSaver,
    encrypt_serde,
    keys_from_env,
)
from course_discovery.privacy.registry import forget_threads, stale_threads


def msgpack_allowlist() -> list[tuple[str, str]]:
    return [
        (cls.__module__, cls.__name__)
        for _, cls in inspect.getmembers(domain_models, inspect.isclass)
        if issubclass(cls, (BaseModel, Enum)) and cls.__module__ == domain_models.__name__
    ]


def build_cipher() -> AesGcmCipher | None:
    keys = keys_from_env()
    return AesGcmCipher(keys) if keys else None


def build_serde(cipher: AesGcmCipher | None = None) -> SerializerProtocol:
    base = JsonPlusSerializer(allowed_msgpack_modules=msgpack_allowlist())
    return encrypt_serde(base, cipher)


def memory_saver() -> MemorySaver:
    return MemorySaver(serde=build_serde(build_cipher()))


@asynccontextmanager
async def open_checkpointer(database_url: str | None = None) -> AsyncIterator[BaseCheckpointSaver]:
    url = database_url or os.getenv("DATABASE_URL")
    if not url:
        yield memory_saver()
        return
    cipher = build_cipher()
    serde = build_serde(cipher)
    async with await AsyncConnection.connect(
        url, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = AsyncPostgresSaver(conn=conn, serde=serde)
        await saver.setup()
        yield SealingSaver(saver, cipher) if cipher else saver


async def prune_checkpoints(
    saver: BaseCheckpointSaver, older_than_days: float, now: datetime | None = None
) -> list[str]:
    saver = saver.inner if isinstance(saver, SealingSaver) else saver
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=older_than_days)
    thread_ids = stale_threads(cutoff)
    for thread_id in thread_ids:
        await saver.adelete_thread(thread_id)
    forget_threads(thread_ids)
    return thread_ids
