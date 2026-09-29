from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

from opentelemetry import context, propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)

from course_discovery.observability.logging import get_logger

logger = get_logger(__name__)

TRACER_NAME = "course_discovery"

_provider: TracerProvider | None = None


def _enabled() -> bool:
    return os.getenv("OTEL_SDK_DISABLED", "").lower() != "true"


def capture_content() -> bool:
    return os.getenv("OTEL_CAPTURE_CONTENT", "").lower() == "true"


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
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        if exporter is not None:
            provider.add_span_processor(SimpleSpanProcessor(exporter))
        elif (configured := _exporter()) is not None:
            provider.add_span_processor(BatchSpanProcessor(configured))
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


@contextmanager
def run_span(
    name: str, *, run_id: str, thread_id: str, resume: bool = False
) -> Iterator[trace.Span]:
    with tracer().start_as_current_span(
        name,
        attributes={
            "course.run_id": run_id,
            "course.thread_id": thread_id,
            "course.resume": resume,
        },
    ) as span:
        yield span
