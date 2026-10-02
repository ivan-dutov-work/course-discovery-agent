from __future__ import annotations

import os
import time
from collections import OrderedDict
from contextlib import contextmanager
from typing import Any, Iterator, Mapping

from opentelemetry import context, propagate, trace
from opentelemetry.trace import Link
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from course_discovery.observability.logging import capture_content, get_logger
from course_discovery.observability.metrics import flush_metrics, record_run_duration
from course_discovery.observability.resource import build_resource

logger = get_logger(__name__)

TRACER_NAME = "course_discovery"

_provider: TracerProvider | None = None

_BATCH_DELAY_MS = int(os.getenv("OTEL_BSP_SCHEDULE_DELAY", "1000"))
_FLUSH_TIMEOUT_MS = 2000


def _enabled() -> bool:
    return os.getenv("OTEL_SDK_DISABLED", "").lower() != "true"


def _exporter() -> SpanExporter | None:
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"
    ):
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter()
    if os.getenv("OTEL_TRACES_EXPORTER", "").lower() == "console":
        return ConsoleSpanExporter()
    return None


def instrument(provider: TracerProvider) -> None:
    from openinference.instrumentation import TraceConfig
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    hide = not capture_content()
    LangChainInstrumentor().instrument(
        tracer_provider=provider,
        config=TraceConfig(hide_inputs=hide, hide_outputs=hide),
    )
    PsycopgInstrumentor().instrument(
        tracer_provider=provider, enable_commenter=False, capture_parameters=False
    )


def configure_tracing(
    service_name: str, *, exporter: SpanExporter | None = None
) -> TracerProvider | None:
    """Install the tracer provider once. Telemetry failures never reach the run."""
    global _provider
    if _provider is not None or not _enabled():
        return _provider
    try:
        provider = TracerProvider(resource=build_resource(service_name))
        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        elif (configured := _exporter()) is not None:
            provider.add_span_processor(
                BatchSpanProcessor(configured, schedule_delay_millis=_BATCH_DELAY_MS)
            )
        trace.set_tracer_provider(provider)
        instrument(provider)
    except Exception:
        logger.warning(
            "tracing_setup_failed",
            extra={"event": "tracing.setup_failed"},
            exc_info=True,
        )
        return None
    _provider = provider
    return provider


def shutdown_tracing() -> None:
    global _provider
    if _provider is None:
        return
    try:
        _provider.shutdown()
    except Exception:
        logger.warning(
            "tracing_shutdown_failed",
            extra={"event": "tracing.shutdown_failed"},
            exc_info=True,
        )
    _provider = None
    uninstrument()


def uninstrument() -> None:
    from openinference.instrumentation.langchain import LangChainInstrumentor
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

    LangChainInstrumentor().uninstrument()
    PsycopgInstrumentor().uninstrument()


def flush_tracing() -> None:
    if _provider is None:
        return
    try:
        _provider.force_flush(timeout_millis=_FLUSH_TIMEOUT_MS)
    except Exception:
        logger.warning(
            "tracing_flush_failed", extra={"event": "tracing.flush_failed"}, exc_info=True
        )


def tracer() -> trace.Tracer:
    return (_provider or trace.get_tracer_provider()).get_tracer(TRACER_NAME)


TRACE_CONTEXT_KEY = "_traceparent"


def inject_trace_context(payload: dict) -> dict:
    carrier: dict[str, str] = {}
    propagate.inject(carrier)
    traceparent = carrier.get("traceparent")
    if traceparent is None or TRACE_CONTEXT_KEY in payload:
        return payload
    return {**payload, TRACE_CONTEXT_KEY: traceparent}


def extract_trace_context(payload: dict) -> context.Context | None:
    traceparent = payload.get(TRACE_CONTEXT_KEY)
    if not traceparent:
        return None
    return propagate.extract({"traceparent": traceparent})


_MAX_TRACKED_RUNS = 128
_last_segment: OrderedDict[str, trace.SpanContext] = OrderedDict()


def _remember_segment(run_id: str, span_context: trace.SpanContext) -> None:
    _last_segment[run_id] = span_context
    _last_segment.move_to_end(run_id)
    while len(_last_segment) > _MAX_TRACKED_RUNS:
        _last_segment.popitem(last=False)


def _plain(value: Any) -> Any:
    return getattr(value, "value", value)


def annotate_run(span: trace.Span, values: Mapping[str, Any]) -> None:
    metrics = values.get("metrics")
    attributes = {
        "course.routing_decision": _plain(values.get("routing_decision")),
        "course.publish_status": _plain(values.get("publish_status")),
        "course.review_iteration": len(values.get("feedback_history") or []),
        "course.research_iteration": values.get("research_iteration"),
        "course.cache_candidates": getattr(metrics, "cache_hits", None),
        "course.tavily_calls": getattr(metrics, "tavily_calls", None),
        "course.valid_count": len(values.get("valid_courses") or []),
        "course.rejected_count": len(values.get("rejected_courses") or []),
        "course.uncertain_count": len(values.get("uncertain_courses") or []),
    }
    span.set_attributes({k: v for k, v in attributes.items() if v is not None})


@contextmanager
def run_span(
    name: str, *, run_id: str, thread_id: str, resume: bool = False
) -> Iterator[trace.Span]:
    start = time.perf_counter()
    outcome = "ok"
    previous = _last_segment.get(run_id) if resume else None
    try:
        with tracer().start_as_current_span(
            name,
            attributes={
                "course.run_id": run_id,
                "course.thread_id": thread_id,
                "course.resume": resume,
            },
            links=[Link(previous)] if previous else None,
        ) as span:
            _remember_segment(run_id, span.get_span_context())
            try:
                yield span
            except Exception:
                outcome = "error"
                raise
            except BaseException:
                outcome = "aborted"
                raise
    finally:
        record_run_duration(time.perf_counter() - start, resume=resume, outcome=outcome)
        flush_tracing()
        flush_metrics()
