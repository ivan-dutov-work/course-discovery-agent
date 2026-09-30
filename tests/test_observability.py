from __future__ import annotations

import contextlib
import io
import json
import logging
import os
import unittest
from datetime import timedelta
from unittest.mock import patch
from uuid import uuid4

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.runnables import RunnableConfig
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export import SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from course_discovery.app.llm import FALLBACK_MODELS, PRIMARY_MODEL, ServedModelLogger
from course_discovery.app.cli import _initial_state, _stream_until_pause
from course_discovery.domain.models import DeliveryStatus
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway, OutboxGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.models import Effect, PermanentEffectError
from course_discovery.effects.worker import OutboxWorker, WorkerStats, utcnow
from course_discovery.observability.logging import JsonFormatter
from course_discovery.observability import tracing
from course_discovery.observability.metrics import (
    configure_metrics,
    register_outbox_gauges,
    shutdown_metrics,
)
from course_discovery.observability.tracing import (
    configure_tracing,
    run_span,
    shutdown_tracing,
)
from course_discovery.workflows.outer_graph import build_graph

QUERY = "Find free Python courses with certificate for beginners"


def _config(run_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": run_id}}


class JsonLines(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.setFormatter(JsonFormatter())
        self.lines: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(json.loads(self.format(record)))


class FailingExporter:
    def export(self, spans):
        raise ConnectionError("collector down")

    def shutdown(self) -> None:
        pass

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True


class TracingTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for name in ("OPENROUTER_API_KEY", "DATABASE_URL", "OTEL_SDK_DISABLED"):
            os.environ.pop(name, None)
        self.exporter = InMemorySpanExporter()
        configure_tracing("test", exporter=self.exporter)
        self.addCleanup(shutdown_tracing)
        self.addCleanup(set_gateway, None)
        self.store = InMemoryOutboxStore()
        self.delivered: list[Effect] = []
        self.worker = OutboxWorker(
            self.store, {"publish_digest": self.delivered.append}, backoff=lambda _: 0.0
        )

    def spans(self, name: str):
        return [s for s in self.exporter.get_finished_spans() if s.name == name]

    async def run_to_publish(self, run_id: str) -> dict:
        graph = build_graph()
        config = _config(run_id)
        with contextlib.redirect_stdout(io.StringIO()):
            paused = await _stream_until_pause(
                graph, _initial_state(QUERY, run_id), config, resume=False
            )
            self.paused = paused
            self.pending = (await graph.aget_state(config)).next
            await graph.aupdate_state(config, {"manager_feedback": "approve"})
            return await _stream_until_pause(graph, None, config, resume=True)


class RunTraceTests(TracingTestCase):
    async def test_review_pause_splits_a_run_into_two_traces_sharing_run_id(self):
        set_gateway(InlineGateway(self.store, self.worker))

        result = await self.run_to_publish("run-trace")

        self.assertEqual(self.pending, ("await_human_review",))
        self.assertEqual(result["publish_status"], DeliveryStatus.DELIVERED)

        (start,), (resume,) = self.spans("run.start"), self.spans("run.resume")
        self.assertNotEqual(start.context.trace_id, resume.context.trace_id)
        for span in (start, resume):
            self.assertEqual(span.attributes["course.run_id"], "run-trace")

        first = {
            s.name
            for s in self.exporter.get_finished_spans()
            if s.context.trace_id == start.context.trace_id
        }
        self.assertTrue(
            {"course_research", "search_web_for_courses", "verify_course_claims", "rank_and_summarize_courses"} <= first
        )

        second = {
            s.name
            for s in self.exporter.get_finished_spans()
            if s.context.trace_id == resume.context.trace_id
        }
        self.assertTrue({"send_approved_courses", "effect.submit", "effect.deliver"} <= second)

    async def test_delivery_in_another_context_joins_the_submitting_trace(self):
        gateway = OutboxGateway(self.store)
        with run_span("run.start", run_id="r", thread_id="r"):
            gateway.submit(Effect("publish:r", "publish_digest", {"digest": "d"}))
        self.assertEqual(self.delivered, [])

        self.worker.run_once()

        (submit,), (deliver,) = self.spans("effect.submit"), self.spans("effect.deliver")
        self.assertEqual(deliver.context.trace_id, submit.context.trace_id)
        self.assertEqual(deliver.parent.span_id, submit.context.span_id)
        self.assertEqual(deliver.attributes["effect.attempt"], 1)

    async def test_failed_delivery_marks_span_and_next_attempt_is_visible(self):
        calls = []

        def flaky(effect: Effect) -> None:
            calls.append(effect)
            if len(calls) == 1:
                raise ConnectionError("downstream unavailable")

        worker = OutboxWorker(self.store, {"publish_digest": flaky}, backoff=lambda _: 0.0)
        OutboxGateway(self.store).submit(Effect("publish:r2", "publish_digest", {}))

        worker.run_once()
        worker.run_once()

        first, second = sorted(self.spans("effect.deliver"), key=lambda s: s.start_time)
        self.assertFalse(first.status.is_ok)
        self.assertEqual([first.attributes["effect.attempt"], second.attributes["effect.attempt"]], [1, 2])

    async def test_no_query_or_digest_content_reaches_span_attributes(self):
        set_gateway(InlineGateway(self.store, self.worker))

        result = await self.run_to_publish("run-content")

        values = [
            str(v)
            for s in self.exporter.get_finished_spans()
            for v in (s.attributes or {}).values()
        ]
        self.assertTrue(values)
        self.assertFalse([v for v in values if "Python" in v or result["digest"][:40] in v])

    async def test_logs_inside_a_run_carry_the_trace_id(self):
        set_gateway(InlineGateway(self.store, self.worker))
        handler = JsonLines()
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        root_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.INFO)
        self.addCleanup(logging.getLogger().setLevel, root_level)

        await self.run_to_publish("run-logs")

        (start,) = self.spans("run.start")
        search = [l for l in handler.lines if l.get("event") == "tavily.search_complete"]
        self.assertTrue(search)
        self.assertEqual({l["trace_id"] for l in search}, {format(start.context.trace_id, "032x")})


class FailOpenTests(unittest.IsolatedAsyncioTestCase):
    async def test_dead_collector_does_not_fail_the_run(self):
        with patch.dict(os.environ, {}, clear=False):
            for name in ("OPENROUTER_API_KEY", "DATABASE_URL", "OTEL_SDK_DISABLED"):
                os.environ.pop(name, None)
            configure_tracing("test", exporter=FailingExporter())
            self.addCleanup(shutdown_tracing)
            self.addCleanup(set_gateway, None)
            store = InMemoryOutboxStore()
            worker = OutboxWorker(store, {"publish_digest": lambda e: None})
            set_gateway(InlineGateway(store, worker))
            graph = build_graph()

            with contextlib.redirect_stdout(io.StringIO()), self.assertLogs("opentelemetry", "ERROR"):
                await _stream_until_pause(
                    graph, _initial_state(QUERY, "run-dead"), _config("run-dead"), resume=False
                )
                await graph.aupdate_state(_config("run-dead"), {"manager_feedback": "approve"})
                result = await _stream_until_pause(graph, None, _config("run-dead"), resume=True)

        self.assertEqual(result["publish_status"], DeliveryStatus.DELIVERED)


if __name__ == "__main__":
    unittest.main()


class MetricsTestCase(TracingTestCase):
    def setUp(self):
        super().setUp()
        self.reader = InMemoryMetricReader()
        configure_metrics("test", reader=self.reader)
        self.addCleanup(shutdown_metrics)

    def points(self, name: str) -> list:
        data = self.reader.get_metrics_data()
        return [
            point
            for resource in (data.resource_metrics if data else [])
            for scope in resource.scope_metrics
            for metric in scope.metrics
            if metric.name == name
            for point in metric.data.data_points
        ]


class MetricsTests(MetricsTestCase):
    async def test_run_records_cache_lookup_and_a_duration_per_segment(self):
        set_gateway(InlineGateway(self.store, self.worker))

        await self.run_to_publish("run-metrics")

        (lookups,) = self.points("course.cache.lookups")
        self.assertEqual(lookups.value, 1)
        self.assertEqual(lookups.attributes, {"outcome": "hit"})
        (candidates,) = self.points("course.cache.candidates")
        self.assertEqual(candidates.count, 1)
        durations = {p.attributes["course.resume"]: p for p in self.points("course.run.duration")}
        self.assertEqual(set(durations), {False, True})
        self.assertEqual({p.attributes["outcome"] for p in durations.values()}, {"ok"})
        self.assertTrue(all(p.count == 1 and p.sum > 0 for p in durations.values()))

    async def test_fallback_counter_only_counts_calls_served_by_a_fallback_model(self):
        def served(model: str) -> LLMResult:
            message = AIMessage(content="x", response_metadata={"model_name": model})
            return LLMResult(generations=[[ChatGeneration(message=message)]])

        handler = ServedModelLogger("planner")
        handler.on_llm_end(served(PRIMARY_MODEL))
        handler.on_llm_end(served(FALLBACK_MODELS[0]))

        (calls,), (fallbacks,) = self.points("llm.calls"), self.points("llm.fallbacks")
        self.assertEqual((calls.value, fallbacks.value), (2, 1))
        self.assertEqual(fallbacks.attributes, {"node": "planner"})

    async def test_versioned_primary_model_name_is_not_a_fallback(self):
        message = AIMessage(content="x", response_metadata={"model_name": f"{PRIMARY_MODEL}-20260301"})
        ServedModelLogger("planner").on_llm_end(
            LLMResult(generations=[[ChatGeneration(message=message)]])
        )

        self.assertEqual(self.points("llm.fallbacks"), [])

    async def test_llm_call_records_duration_tokens_and_errors(self):
        run_ok, run_bad = uuid4(), uuid4()
        handler = ServedModelLogger("planner")
        message = AIMessage(
            content="x",
            response_metadata={"model_name": PRIMARY_MODEL},
            usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
        )

        handler.on_llm_start({}, ["p"], run_id=run_ok)
        handler.on_llm_end(LLMResult(generations=[[ChatGeneration(message=message)]]), run_id=run_ok)
        handler.on_llm_start({}, ["p"], run_id=run_bad)
        handler.on_llm_error(TimeoutError("slow"), run_id=run_bad)

        outcomes = {
            (p.attributes["outcome"], p.attributes.get("error.type")): p.value
            for p in self.points("llm.calls")
        }
        self.assertEqual(outcomes, {("ok", None): 1, ("error", "TimeoutError"): 1})
        tokens = {p.attributes["gen_ai.token.type"]: p.sum for p in self.points("gen_ai.client.token.usage")}
        self.assertEqual(tokens, {"input": 120, "output": 30})
        self.assertEqual(sum(p.count for p in self.points("gen_ai.client.operation.duration")), 2)

    async def test_run_duration_is_labelled_with_the_failure_outcome(self):
        with self.assertRaises(RuntimeError):
            with run_span("run.start", run_id="r-fail", thread_id="r-fail"):
                raise RuntimeError("boom")

        (point,) = self.points("course.run.duration")
        self.assertEqual(point.attributes["outcome"], "error")

    async def test_degradation_paths_are_counted(self):
        set_gateway(InlineGateway(self.store, self.worker))
        graph = build_graph()
        config = _config("run-degraded")
        with contextlib.redirect_stdout(io.StringIO()):
            await _stream_until_pause(graph, _initial_state(QUERY, "run-degraded"), config, resume=False)
            await graph.aupdate_state(config, {"manager_feedback": ""})
            await _stream_until_pause(graph, None, config, resume=True)

        degraded = {
            (p.attributes["component"], p.attributes["reason"]): p.value
            for p in self.points("course.degradations")
        }
        self.assertEqual(degraded[("router", "no_feedback")], 1)
        decisions = {p.attributes["action"]: p.value for p in self.points("course.review.decisions")}
        self.assertEqual(decisions, {"DISCARD": 1})

    async def test_run_records_validation_verdicts_and_digest_sources(self):
        set_gateway(InlineGateway(self.store, self.worker))

        await self.run_to_publish("run-verdicts")

        verdicts = {p.attributes["status"] for p in self.points("course.validation.candidates")}
        self.assertEqual(verdicts, {"valid", "rejected", "uncertain"})
        self.assertTrue(self.points("course.digest.courses"))

    async def test_run_annotates_the_span_and_links_resume_to_start(self):
        set_gateway(InlineGateway(self.store, self.worker))

        await self.run_to_publish("run-annotated")

        (start,), (resume,) = self.spans("run.start"), self.spans("run.resume")
        self.assertGreater(start.attributes["course.valid_count"], 0)
        self.assertEqual(resume.attributes["course.routing_decision"], "PUBLISH")
        self.assertEqual([l.context.span_id for l in resume.links], [start.context.span_id])

    async def test_outbox_counts_outcomes_and_dead_letter_reasons(self):
        def broken(effect: Effect) -> None:
            raise PermanentEffectError("rejected")

        worker = OutboxWorker(
            self.store,
            {"publish_digest": self.delivered.append, "broken": broken},
            backoff=lambda _: 0.0,
        )
        gateway = OutboxGateway(self.store)
        gateway.submit(Effect("publish:ok", "publish_digest", {}))
        gateway.submit(Effect("broken:1", "broken", {}))
        gateway.submit(Effect("orphan:1", "unknown_kind", {}))

        worker.run_once()

        counted = {
            (p.attributes["outcome"], p.attributes.get("reason")): p.value
            for p in self.points("outbox.effects")
        }
        self.assertEqual(
            counted,
            {("delivered", None): 1, ("dead", "permanent"): 1, ("dead", "no_handler"): 1},
        )
        (latency,) = self.points("outbox.delivery.latency")
        self.assertEqual(latency.count, 1)

    async def test_outbox_gauges_report_backlog_dead_letters_and_age(self):
        gateway = OutboxGateway(self.store)
        gateway.submit(Effect("publish:waiting", "publish_digest", {}))
        gateway.submit(Effect("orphan:1", "unknown_kind", {}))
        worker = OutboxWorker(self.store, {"publish_digest": self.delivered.append})
        self.store.claim(worker.clock(), 10, 60.0)
        worker._process(self.store.get("orphan:1"), WorkerStats())
        register_outbox_gauges(lambda: self.store.stats(utcnow() + timedelta(seconds=30)))

        gauges = {
            name: self.points(name)[0].value
            for name in ("outbox.pending", "outbox.dead_letters", "outbox.oldest_pending_age")
        }

        self.assertEqual(gauges["outbox.pending"], 1)
        self.assertEqual(gauges["outbox.dead_letters"], 1)
        self.assertGreaterEqual(gauges["outbox.oldest_pending_age"], 30)

    async def test_failed_ack_after_delivery_is_counted_and_reraised(self):
        class BrokenAck(InMemoryOutboxStore):
            def mark_delivered(self, key, now):
                raise ConnectionError("db gone")

        store = BrokenAck()
        OutboxGateway(store).submit(Effect("publish:ok", "publish_digest", {}))
        worker = OutboxWorker(store, {"publish_digest": self.delivered.append})

        with self.assertRaises(ConnectionError):
            worker.run_once()

        (point,) = self.points("outbox.effects")
        self.assertEqual(point.attributes, {"outcome": "ack_failed"})


class QueryContentTests(TracingTestCase):
    async def test_previews_are_absent_from_logs_unless_content_capture_is_on(self):
        set_gateway(InlineGateway(self.store, self.worker))
        handler = JsonLines()
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        root_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.INFO)
        self.addCleanup(logging.getLogger().setLevel, root_level)

        await self.run_to_publish("run-preview-logs")

        previews = [l for l in handler.lines if "query_preview" in l or "feedback_preview" in l]
        self.assertTrue(previews)
        self.assertTrue(all(v is None for l in previews for k, v in l.items() if k.endswith("_preview")))
        self.assertTrue(all("taskName" not in l for l in handler.lines))

    async def test_search_log_events_carry_query_length_not_query_text(self):
        set_gateway(InlineGateway(self.store, self.worker))
        handler = JsonLines()
        logging.getLogger().addHandler(handler)
        self.addCleanup(logging.getLogger().removeHandler, handler)
        root_level = logging.getLogger().level
        logging.getLogger().setLevel(logging.INFO)
        self.addCleanup(logging.getLogger().setLevel, root_level)

        await self.run_to_publish("run-query-logs")

        searches = [l for l in handler.lines if str(l.get("event", "")).startswith("tavily.")]
        self.assertTrue(searches)
        for line in searches:
            self.assertIsInstance(line["query_len"], int)
            self.assertNotIn("query", line)


class FlushTests(unittest.IsolatedAsyncioTestCase):
    async def test_spans_are_exported_when_a_run_segment_ends_without_waiting_for_the_batch(self):
        exporter = InMemorySpanExporter()
        with patch.dict(os.environ, {}, clear=False), patch.object(
            tracing, "_exporter", return_value=exporter
        ), patch.object(tracing, "_BATCH_DELAY_MS", 600_000):
            os.environ.pop("OTEL_SDK_DISABLED", None)
            configure_tracing("test")
            self.addCleanup(shutdown_tracing)

            with run_span("run.start", run_id="r", thread_id="r"):
                pass

            self.assertEqual([s.name for s in exporter.get_finished_spans()], ["run.start"])
