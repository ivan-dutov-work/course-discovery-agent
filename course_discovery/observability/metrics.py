from __future__ import annotations

import os
from typing import TYPE_CHECKING, Callable, Iterable, Mapping

from opentelemetry import metrics
from opentelemetry.metrics import CallbackOptions, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.metrics.view import ExplicitBucketHistogramAggregation, View

from course_discovery.observability.logging import get_logger
from course_discovery.observability.resource import build_resource

if TYPE_CHECKING:
    from course_discovery.effects.models import OutboxStats

logger = get_logger(__name__)

METER_NAME = "course_discovery"
FLUSH_TIMEOUT_MS = 2000

_BUCKETS: dict[str, list[float]] = {
    "course.run.duration": [1, 2, 5, 10, 20, 30, 45, 60, 90, 120, 180, 300],
    "course.review.wait": [5, 15, 30, 60, 120, 300, 600, 1800, 3600],
    "course.search.duration": [0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10],
    "course.cache.candidates": [0, 1, 2, 5, 10, 20, 50],
    "gen_ai.client.operation.duration": [0.25, 0.5, 1, 2, 4, 8, 16, 30, 60],
    "gen_ai.client.token.usage": [1, 4, 16, 64, 256, 1024, 4096, 16384, 65536],
    "outbox.delivery.latency": [0.1, 0.5, 1, 5, 15, 60, 300, 900, 3600],
}

_provider: MeterProvider | None = None
_cached: tuple[object, "_Instruments"] | None = None


def _reader() -> MetricReader | None:
    kind = os.getenv("OTEL_METRICS_EXPORTER", "").lower()
    if kind == "otlp":
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter,
        )

        return PeriodicExportingMetricReader(OTLPMetricExporter())
    if kind == "console":
        return PeriodicExportingMetricReader(ConsoleMetricExporter())
    return None


def _views() -> list[View]:
    return [
        View(
            instrument_name=name,
            aggregation=ExplicitBucketHistogramAggregation(boundaries),
        )
        for name, boundaries in _BUCKETS.items()
    ]


def configure_metrics(
    service_name: str, *, reader: MetricReader | None = None
) -> MeterProvider | None:
    """Install the meter provider once. Off unless OTEL_METRICS_EXPORTER is otlp or console."""
    global _provider
    if _provider is not None or os.getenv("OTEL_SDK_DISABLED", "").lower() == "true":
        return _provider
    try:
        configured = reader or _reader()
        if configured is None:
            return None
        _provider = MeterProvider(
            resource=build_resource(service_name),
            metric_readers=[configured],
            views=_views(),
        )
    except Exception:
        logger.warning(
            "metrics_setup_failed",
            extra={"event": "metrics.setup_failed"},
            exc_info=True,
        )
        return None
    return _provider


def shutdown_metrics() -> None:
    global _provider
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception:
        logger.warning(
            "metrics_shutdown_failed",
            extra={"event": "metrics.shutdown_failed"},
            exc_info=True,
        )
    _provider = None


def flush_metrics() -> None:
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=FLUSH_TIMEOUT_MS)
    except Exception:
        logger.warning(
            "metrics_flush_failed", extra={"event": "metrics.flush_failed"}, exc_info=True
        )


class _Instruments:
    def __init__(self, meter: metrics.Meter) -> None:
        self.meter = meter
        counter = meter.create_counter
        histogram = meter.create_histogram
        self.cache_lookups = counter(
            "course.cache.lookups", description="Cache lookups by outcome (hit = any candidate)"
        )
        self.cache_candidates = histogram(
            "course.cache.candidates", unit="{candidate}", description="Candidates per cache lookup"
        )
        self.digest_courses = counter(
            "course.digest.courses", unit="{course}", description="Courses in a digest by source"
        )
        self.validation_candidates = counter(
            "course.validation.candidates",
            unit="{candidate}",
            description="Validator verdicts by status",
        )
        self.replans = counter("course.research.replans", description="Replanner passes")
        self.search_calls = counter(
            "course.search.calls", description="Search worker calls by outcome"
        )
        self.search_duration = histogram("course.search.duration", unit="s")
        self.run_duration = histogram(
            "course.run.duration", unit="s", description="Run segment wall time, review wait excluded"
        )
        self.run_outcomes = counter("course.run.outcomes", description="Terminal run outcomes")
        self.review_wait = histogram(
            "course.review.wait", unit="s", description="Time a run sat at the review gate"
        )
        self.review_decisions = counter(
            "course.review.decisions", description="Routing actions taken after review"
        )
        self.degradations = counter(
            "course.degradations",
            description="Fail-closed or degraded paths taken, by component and reason",
        )
        self.db_errors = counter("course.db.errors", description="Failed DB operations")
        self.transient_errors = counter(
            "resilience.transient_errors",
            description="Failures RetryPolicy classified as retryable, including the final attempt",
        )
        self.llm_calls = counter("llm.calls", description="LLM invocations by outcome")
        self.llm_fallbacks = counter("llm.fallbacks", description="Calls served by a fallback model")
        self.llm_duration = histogram("gen_ai.client.operation.duration", unit="s")
        self.llm_tokens = histogram("gen_ai.client.token.usage", unit="{token}")
        self.outbox_effects = counter("outbox.effects", description="Delivery outcomes")
        self.outbox_latency = histogram(
            "outbox.delivery.latency", unit="s", description="Enqueue to delivered"
        )


def _instruments() -> _Instruments:
    global _cached
    provider = _provider or metrics.get_meter_provider()
    if _cached is None or _cached[0] is not provider:
        _cached = (provider, _Instruments(provider.get_meter(METER_NAME)))
    return _cached[1]


def record_cache_lookup(candidate_count: int) -> None:
    instruments = _instruments()
    instruments.cache_lookups.add(1, {"outcome": "hit" if candidate_count else "miss"})
    instruments.cache_candidates.record(candidate_count)


def record_digest_sources(counts: Mapping[str, int]) -> None:
    for source, count in counts.items():
        _instruments().digest_courses.add(count, {"source": source})


def record_validation(*, valid: int, rejected: int, uncertain: int) -> None:
    counter = _instruments().validation_candidates
    for status, count in (("valid", valid), ("rejected", rejected), ("uncertain", uncertain)):
        counter.add(count, {"status": status})


def record_replan() -> None:
    _instruments().replans.add(1)


def record_search(outcome: str, seconds: float) -> None:
    instruments = _instruments()
    instruments.search_calls.add(1, {"outcome": outcome})
    instruments.search_duration.record(seconds, {"outcome": outcome})


def record_run_duration(seconds: float, *, resume: bool, outcome: str = "ok") -> None:
    _instruments().run_duration.record(seconds, {"course.resume": resume, "outcome": outcome})


def record_run_outcome(outcome: str, **attributes: str) -> None:
    _instruments().run_outcomes.add(1, {"outcome": outcome, **attributes})


def record_review_wait(seconds: float) -> None:
    _instruments().review_wait.record(seconds)


def record_review_decision(action: str) -> None:
    _instruments().review_decisions.add(1, {"action": action})


def record_degradation(component: str, reason: str) -> None:
    _instruments().degradations.add(1, {"component": component, "reason": reason})


def record_db_error(operation: str) -> None:
    _instruments().db_errors.add(1, {"operation": operation})


def record_transient_error(error_type: str) -> None:
    _instruments().transient_errors.add(1, {"error.type": error_type})


def record_llm_call(
    node: str,
    *,
    requested_model: str,
    served_model: str | None,
    fell_back: bool,
    seconds: float | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> None:
    instruments = _instruments()
    instruments.llm_calls.add(1, {"node": node, "outcome": "ok"})
    if fell_back:
        instruments.llm_fallbacks.add(1, {"node": node})
    attributes = {
        "node": node,
        "gen_ai.operation.name": "chat",
        "gen_ai.request.model": requested_model,
        "gen_ai.response.model": served_model or "unknown",
    }
    if seconds is not None:
        instruments.llm_duration.record(seconds, attributes)
    for token_type, count in (("input", input_tokens), ("output", output_tokens)):
        if count is not None:
            instruments.llm_tokens.record(count, {**attributes, "gen_ai.token.type": token_type})


def record_llm_error(
    node: str, *, requested_model: str, error_type: str, seconds: float | None = None
) -> None:
    instruments = _instruments()
    instruments.llm_calls.add(1, {"node": node, "outcome": "error", "error.type": error_type})
    if seconds is not None:
        instruments.llm_duration.record(
            seconds,
            {
                "node": node,
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": requested_model,
                "error.type": error_type,
            },
        )


def record_effect_outcome(outcome: str, *, reason: str | None = None) -> None:
    attributes = {"outcome": outcome}
    if reason:
        attributes["reason"] = reason
    _instruments().outbox_effects.add(1, attributes)


def record_delivery_latency(seconds: float) -> None:
    _instruments().outbox_latency.record(seconds)


def register_outbox_gauges(read: Callable[[], "OutboxStats"]) -> None:
    meter = _instruments().meter

    def observe(select: Callable[["OutboxStats"], float]):
        def callback(_: CallbackOptions) -> Iterable[Observation]:
            try:
                return [Observation(select(read()))]
            except Exception:
                logger.warning(
                    "outbox_stats_failed", extra={"event": "outbox.stats_failed"}, exc_info=True
                )
                return []

        return callback

    meter.create_observable_gauge(
        "outbox.pending",
        callbacks=[observe(lambda s: s.pending)],
        unit="{effect}",
        description="Queued or in-progress effects",
    )
    meter.create_observable_gauge(
        "outbox.dead_letters",
        callbacks=[observe(lambda s: s.dead)],
        unit="{effect}",
        description="Effects awaiting manual requeue",
    )
    meter.create_observable_gauge(
        "outbox.oldest_pending_age",
        callbacks=[observe(lambda s: s.oldest_pending_age_seconds)],
        unit="s",
        description="Age of the oldest undelivered effect; grows when no worker is draining",
    )
