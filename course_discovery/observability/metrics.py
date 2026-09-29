from __future__ import annotations

import os

from opentelemetry import metrics
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

from course_discovery.observability.logging import get_logger

logger = get_logger(__name__)

METER_NAME = "course_discovery"

_provider: MeterProvider | None = None


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
            resource=Resource.create({"service.name": service_name}),
            metric_readers=[configured],
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


def _meter() -> metrics.Meter:
    return (_provider or metrics.get_meter_provider()).get_meter(METER_NAME)


def record_cache_lookup(hits: int) -> None:
    meter = _meter()
    meter.create_counter("course.cache.lookups").add(1)
    meter.create_counter("course.cache.hits").add(hits)


def record_run_duration(seconds: float, *, resume: bool) -> None:
    _meter().create_histogram("course.run.duration", unit="s").record(
        seconds, {"course.resume": resume}
    )


def record_llm_call(node: str, *, fell_back: bool) -> None:
    meter = _meter()
    meter.create_counter("llm.calls").add(1, {"node": node})
    if fell_back:
        meter.create_counter("llm.fallbacks").add(1, {"node": node})


def record_effect_outcome(outcome: str, *, reason: str | None = None) -> None:
    attributes = {"outcome": outcome}
    if reason:
        attributes["reason"] = reason
    _meter().create_counter("outbox.effects").add(1, attributes)
