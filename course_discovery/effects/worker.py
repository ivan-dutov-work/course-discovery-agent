from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Mapping

from course_discovery.effects.models import Effect, OutboxRecord, PermanentEffectError
from course_discovery.effects.store import OutboxStore
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.observability.metrics import record_effect_outcome
from course_discovery.observability.tracing import extract_trace_context, tracer


logger = get_logger(__name__)

Handler = Callable[[Effect], None]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def exponential_backoff(base_seconds: float = 2.0, cap_seconds: float = 300.0):
    def delay(attempts: int) -> float:
        return min(cap_seconds, base_seconds * 2 ** (attempts - 1))

    return delay


@dataclass
class WorkerStats:
    delivered: int = 0
    retried: int = 0
    dead: int = 0


class OutboxWorker:
    def __init__(
        self,
        store: OutboxStore,
        handlers: Mapping[str, Handler],
        *,
        max_attempts: int = 5,
        backoff: Callable[[int], float] | None = None,
        lease_seconds: float = 60.0,
        batch_size: int = 10,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.handlers = handlers
        self.max_attempts = max_attempts
        self.backoff = backoff or exponential_backoff()
        self.lease_seconds = lease_seconds
        self.batch_size = batch_size
        self.clock = clock

    def run_once(self) -> WorkerStats:
        stats = WorkerStats()
        for record in self.store.claim(self.clock(), self.batch_size, self.lease_seconds):
            self._process(record, stats)
        if stats.dead:
            logger.error(
                "effects_dead_lettered",
                extra={"event": "outbox.effects_dead_lettered", "dead": stats.dead},
            )
        return stats

    def run_forever(self, stop: threading.Event, poll_seconds: float = 1.0) -> None:
        while not stop.is_set():
            stats = self.run_once()
            if not (stats.delivered or stats.retried or stats.dead):
                stop.wait(poll_seconds)

    def _process(self, record: OutboxRecord, stats: WorkerStats) -> None:
        key = record.effect.key
        if record.attempts > self.max_attempts:
            self.store.mark_dead(key, record.last_error or "lease expired after final attempt")
            stats.dead += 1
            record_effect_outcome("dead", reason="attempts_exhausted")
            self._log("effect_dead", record, reason="attempts_exhausted")
            return

        handler = self.handlers.get(record.effect.kind)
        if handler is None:
            self.store.mark_dead(key, f"no handler for kind {record.effect.kind!r}")
            stats.dead += 1
            record_effect_outcome("dead", reason="no_handler")
            self._log("effect_dead", record, reason="no_handler")
            return

        try:
            with tracer().start_as_current_span(
                "effect.deliver",
                context=extract_trace_context(record.effect.payload),
                attributes={
                    "effect.kind": record.effect.kind,
                    "effect.key": record.effect.key,
                    "effect.attempt": record.attempts,
                },
            ):
                handler(record.effect)
        except PermanentEffectError as exc:
            self.store.mark_dead(key, str(exc))
            stats.dead += 1
            record_effect_outcome("dead", reason="permanent")
            self._log("effect_dead", record, reason="permanent", **sanitize_error(exc))
        except Exception as exc:  # noqa: BLE001
            error = f"{type(exc).__name__}: {exc}"
            if record.attempts >= self.max_attempts:
                self.store.mark_dead(key, error)
                stats.dead += 1
                record_effect_outcome("dead", reason="attempts_exhausted")
                self._log("effect_dead", record, reason="attempts_exhausted", **sanitize_error(exc))
            else:
                retry_at = self.clock() + timedelta(seconds=self.backoff(record.attempts))
                self.store.release(key, error, retry_at)
                stats.retried += 1
                record_effect_outcome("retried")
                self._log("effect_retry", record, **sanitize_error(exc))
        else:
            self.store.mark_delivered(key, self.clock())
            stats.delivered += 1
            record_effect_outcome("delivered")
            self._log("effect_delivered", record)

    def _log(self, event: str, record: OutboxRecord, **extra) -> None:
        logger.info(
            event,
            extra={
                "event": f"outbox.{event}",
                "effect_key": record.effect.key,
                "kind": record.effect.kind,
                "attempts": record.attempts,
                **extra,
            },
        )
