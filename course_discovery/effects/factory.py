from __future__ import annotations

import os

from course_discovery.effects.gateway import EffectGateway, InlineGateway, OutboxGateway
from course_discovery.effects.handlers import default_handlers
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker

_gateway: EffectGateway | None = None


def build_gateway() -> EffectGateway:
    mode = os.getenv("EFFECT_GATEWAY", "inline")
    if mode == "inline":
        store = InMemoryOutboxStore()
        return InlineGateway(store, OutboxWorker(store, default_handlers()))
    if mode == "outbox":
        database_url = os.getenv("DATABASE_URL")
        if not database_url:
            raise RuntimeError("EFFECT_GATEWAY=outbox requires DATABASE_URL")
        return OutboxGateway(PostgresOutboxStore(database_url))
    raise RuntimeError(f"unknown EFFECT_GATEWAY {mode!r}")


def get_gateway() -> EffectGateway:
    global _gateway
    if _gateway is None:
        _gateway = build_gateway()
    return _gateway


def set_gateway(gateway: EffectGateway | None) -> None:
    global _gateway
    _gateway = gateway
