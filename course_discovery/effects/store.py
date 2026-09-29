from __future__ import annotations

from datetime import datetime
from typing import Protocol

from course_discovery.effects.models import Effect, OutboxRecord


class OutboxStore(Protocol):
    def enqueue(self, effect: Effect, now: datetime) -> OutboxRecord:
        """Insert the effect, or return the existing record if the key is already present."""

    def claim(self, now: datetime, limit: int, lease_seconds: float) -> list[OutboxRecord]:
        """Lease due records to one worker and count the attempt."""

    def mark_delivered(self, key: str, now: datetime) -> None: ...

    def release(self, key: str, error: str, retry_at: datetime) -> None: ...

    def mark_dead(self, key: str, error: str) -> None: ...

    def get(self, key: str) -> OutboxRecord | None: ...
