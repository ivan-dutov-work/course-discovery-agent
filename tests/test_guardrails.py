from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from course_discovery.app.cli import _initial_state
from course_discovery.app.gateway import _fallback_parse_filters as _fallback
from course_discovery.app.gateway import gateway_node
from course_discovery.guardrails import PiiGuardrailError, redact_pii, set_redactor
from course_discovery.persistence.checkpointer import memory_saver
from course_discovery.workflows.outer_graph import build_graph
from course_discovery.observability.logging import preview, sanitize_error


RAW_QUERY = (
    "python courses for my colleague Anna Kovalenko, "
    "mail anna.k@example.com or call +380 67 123 45 67"
)
RAW_VALUES = ["Anna Kovalenko", "anna.k@example.com", "123 45 67"]


class FakeRedactor:
    def __init__(self):
        self.calls: list[str] = []

    def redact(self, text: str) -> str:
        self.calls.append(text)
        return text.replace("Anna Kovalenko", "<PERSON>")


class RedactPiiFacadeTests(unittest.TestCase):
    def tearDown(self):
        set_redactor(None)

    def test_delegates_to_configured_redactor(self):
        fake = FakeRedactor()
        set_redactor(fake)
        self.assertEqual(redact_pii("hi Anna Kovalenko"), "hi <PERSON>")
        self.assertEqual(fake.calls, ["hi Anna Kovalenko"])

    def test_none_and_empty_skip_the_backend(self):
        fake = FakeRedactor()
        set_redactor(fake)
        self.assertIsNone(redact_pii(None))
        self.assertEqual(redact_pii(""), "")
        self.assertEqual(fake.calls, [])

    def test_can_be_disabled(self):
        fake = FakeRedactor()
        set_redactor(fake)
        with patch.dict(os.environ, {"PII_GUARDRAIL": "off"}):
            self.assertEqual(redact_pii(RAW_QUERY), RAW_QUERY)
        self.assertEqual(fake.calls, [])

    def test_backend_failure_fails_closed(self):
        class Broken:
            def redact(self, text):
                raise ConnectionError("service down")

        set_redactor(Broken())
        with self.assertRaises(PiiGuardrailError):
            redact_pii(RAW_QUERY)


class BoundaryTests(unittest.TestCase):
    def test_initial_state_never_holds_raw_query(self):
        state = _initial_state(RAW_QUERY)
        for value in RAW_VALUES:
            self.assertNotIn(value, state["user_query"])

    def test_log_preview_masks_pii(self):
        with patch.dict(os.environ, {"OTEL_CAPTURE_CONTENT": "true"}):
            shown = preview(RAW_QUERY, max_len=500)
        for value in RAW_VALUES:
            self.assertNotIn(value, shown)

    def test_error_messages_are_masked(self):
        err = sanitize_error(ValueError(f"bad request for {RAW_QUERY}"))
        for value in RAW_VALUES:
            self.assertNotIn(value, err["error_message"])

    def test_gateway_redacts_before_parsing(self):
        seen: list[str] = []

        def fake_parse(query):
            seen.append(query)
            return _fallback(query)

        state = _initial_state(RAW_QUERY)
        state["user_query"] = RAW_QUERY
        with patch("course_discovery.app.gateway._parse_filters", side_effect=fake_parse):
            gateway_node(state)
        for value in RAW_VALUES:
            self.assertNotIn(value, seen[0])



class CheckpointPlacementTests(unittest.IsolatedAsyncioTestCase):
    async def _persisted_blob(self, state) -> str:
        saver = memory_saver()
        graph = build_graph(checkpointer=saver)
        config = {"configurable": {"thread_id": "pii-placement"}, "recursion_limit": 60}
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            async for _ in graph.astream(state, config):
                pass
        return repr(saver.storage) + repr(saver.blobs) + repr(saver.writes)

    async def test_ingress_redaction_keeps_pii_out_of_every_checkpoint(self):
        blob = await self._persisted_blob(_initial_state(RAW_QUERY))
        for value in RAW_VALUES:
            self.assertNotIn(value, blob)

    async def test_gateway_only_redaction_cannot_keep_pii_out_of_checkpoints(self):
        state = _initial_state("placeholder")
        state["user_query"] = RAW_QUERY
        blob = await self._persisted_blob(state)
        self.assertIn("Anna Kovalenko", blob)


if __name__ == "__main__":
    unittest.main()
