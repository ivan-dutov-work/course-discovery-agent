from __future__ import annotations

from dataclasses import replace
from typing import Callable, Protocol

from datetime import datetime

from course_discovery.domain.models import DeliveryStatus
from course_discovery.effects.models import Effect, OutboxRecord, RecordStatus
from course_discovery.effects.store import OutboxStore
from course_discovery.effects.worker import OutboxWorker, utcnow
from course_discovery.observability.tracing import inject_trace_context, tracer


class EffectGateway(Protocol):
    def submit(self, effect: Effect) -> DeliveryStatus: ...

    def status(self, key: str) -> DeliveryStatus | None: ...


def _traced(effect: Effect) -> Effect:
    return replace(effect, payload=inject_trace_context(effect.payload))


def to_delivery_status(record: OutboxRecord) -> DeliveryStatus:
    if record.status == RecordStatus.DELIVERED:
        return DeliveryStatus.DELIVERED
    if record.status == RecordStatus.DEAD:
        return DeliveryStatus.DEAD
    return DeliveryStatus.QUEUED


class OutboxGateway:
    def __init__(self, store: OutboxStore, clock: Callable[[], datetime] = utcnow) -> None:
        self.store = store
        self.clock = clock

    def submit(self, effect: Effect) -> DeliveryStatus:
        with tracer().start_as_current_span(
            "effect.submit", attributes={"effect.kind": effect.kind, "effect.key": effect.key}
        ):
            return to_delivery_status(self.store.enqueue(_traced(effect), self.clock()))

    def status(self, key: str) -> DeliveryStatus | None:
        record = self.store.get(key)
        return to_delivery_status(record) if record else None


class InlineGateway(OutboxGateway):
    def __init__(self, store: OutboxStore, worker: OutboxWorker) -> None:
        super().__init__(store, worker.clock)
        self.worker = worker

    def submit(self, effect: Effect) -> DeliveryStatus:
        with tracer().start_as_current_span(
            "effect.submit", attributes={"effect.kind": effect.kind, "effect.key": effect.key}
        ):
            enqueued = self.store.enqueue(_traced(effect), self.clock())
            if enqueued.status == RecordStatus.QUEUED:
                self.worker.run_once()
            return self.status(effect.key) or DeliveryStatus.QUEUED
