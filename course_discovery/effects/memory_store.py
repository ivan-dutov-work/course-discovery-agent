from __future__ import annotations

import threading
from dataclasses import replace
from datetime import datetime, timedelta

from course_discovery.effects.models import Effect, OutboxRecord, OutboxStats, RecordStatus


class InMemoryOutboxStore:
    def __init__(self) -> None:
        self._records: dict[str, OutboxRecord] = {}
        self._lock = threading.Lock()

    def enqueue(self, effect: Effect, now: datetime) -> OutboxRecord:
        with self._lock:
            existing = self._records.get(effect.key)
            if existing is not None:
                return existing
            record = OutboxRecord(
                effect=effect,
                status=RecordStatus.QUEUED,
                attempts=0,
                next_attempt_at=now,
                created_at=now,
            )
            self._records[effect.key] = record
            return record

    def claim(self, now: datetime, limit: int, lease_seconds: float) -> list[OutboxRecord]:
        with self._lock:
            due = sorted(
                (
                    record
                    for record in self._records.values()
                    if (
                        record.status == RecordStatus.QUEUED
                        and record.next_attempt_at <= now
                    )
                    or (
                        record.status == RecordStatus.IN_PROGRESS
                        and record.locked_until is not None
                        and record.locked_until <= now
                    )
                ),
                key=lambda record: record.next_attempt_at,
            )[:limit]
            claimed = []
            for record in due:
                leased = replace(
                    record,
                    status=RecordStatus.IN_PROGRESS,
                    attempts=record.attempts + 1,
                    locked_until=now + timedelta(seconds=lease_seconds),
                )
                self._records[record.effect.key] = leased
                claimed.append(leased)
            return claimed

    def mark_delivered(self, key: str, now: datetime) -> None:
        self._update(
            key,
            status=RecordStatus.DELIVERED,
            locked_until=None,
            last_error=None,
            delivered_at=now,
        )

    def release(self, key: str, error: str, retry_at: datetime) -> None:
        self._update(
            key,
            status=RecordStatus.QUEUED,
            locked_until=None,
            last_error=error,
            next_attempt_at=retry_at,
        )

    def mark_dead(self, key: str, error: str) -> None:
        self._update(key, status=RecordStatus.DEAD, locked_until=None, last_error=error)

    def get(self, key: str) -> OutboxRecord | None:
        with self._lock:
            return self._records.get(key)

    def list_dead(self, limit: int) -> list[OutboxRecord]:
        with self._lock:
            dead = [r for r in self._records.values() if r.status == RecordStatus.DEAD]
        return sorted(dead, key=lambda r: r.effect.key)[:limit]

    def requeue(self, key: str, now: datetime) -> bool:
        with self._lock:
            record = self._records.get(key)
            if record is None or record.status != RecordStatus.DEAD:
                return False
            self._records[key] = replace(
                record, status=RecordStatus.QUEUED, attempts=0, next_attempt_at=now
            )
            return True

    def prune_delivered(self, older_than: datetime) -> int:
        with self._lock:
            stale = [
                key
                for key, record in self._records.items()
                if record.status == RecordStatus.DELIVERED
                and record.delivered_at is not None
                and record.delivered_at < older_than
            ]
            for key in stale:
                del self._records[key]
        return len(stale)

    def stats(self, now: datetime) -> OutboxStats:
        with self._lock:
            records = list(self._records.values())
        undelivered = [
            r for r in records if r.status in (RecordStatus.QUEUED, RecordStatus.IN_PROGRESS)
        ]
        created = [r.created_at for r in undelivered if r.created_at is not None]
        return OutboxStats(
            pending=len(undelivered),
            dead=sum(1 for r in records if r.status == RecordStatus.DEAD),
            oldest_pending_age_seconds=(now - min(created)).total_seconds() if created else 0.0,
        )

    def _update(self, key: str, **changes) -> None:
        with self._lock:
            self._records[key] = replace(self._records[key], **changes)
