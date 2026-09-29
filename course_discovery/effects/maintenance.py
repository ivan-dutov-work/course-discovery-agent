from __future__ import annotations

from datetime import datetime, timedelta

from course_discovery.effects.models import OutboxRecord
from course_discovery.effects.store import OutboxStore
from course_discovery.effects.worker import utcnow


def list_dead(store: OutboxStore, limit: int = 50) -> list[OutboxRecord]:
    return store.list_dead(limit)


def requeue(store: OutboxStore, key: str) -> bool:
    return store.requeue(key, utcnow())


def prune_outbox(store: OutboxStore, older_than_days: float, now: datetime | None = None) -> int:
    cutoff = (now or utcnow()) - timedelta(days=older_than_days)
    return store.prune_delivered(cutoff)
