from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class RecordStatus(str, Enum):
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    DELIVERED = "delivered"
    DEAD = "dead"


@dataclass(frozen=True)
class Effect:
    key: str
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboxRecord:
    effect: Effect
    status: RecordStatus
    attempts: int
    next_attempt_at: datetime
    locked_until: datetime | None = None
    last_error: str | None = None
    delivered_at: datetime | None = None
    created_at: datetime | None = None


@dataclass(frozen=True)
class OutboxStats:
    pending: int
    dead: int
    oldest_pending_age_seconds: float


class PermanentEffectError(Exception):
    """Delivery cannot succeed on retry; the worker dead-letters immediately."""
