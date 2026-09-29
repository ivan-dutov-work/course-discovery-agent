from __future__ import annotations

import os
import unittest
from unittest import mock

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult
from langchain_core.rate_limiters import InMemoryRateLimiter

from course_discovery.app.llm import (
    FALLBACK_MODELS,
    PRIMARY_MODEL,
    ServedModelLogger,
    build_llm,
    llm_enabled,
)


class BuildLlmTests(unittest.TestCase):
    def test_request_carries_priority_list_with_primary_first(self) -> None:
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            params = build_llm("gateway")._default_params

        self.assertEqual(params["model"], PRIMARY_MODEL)
        self.assertEqual(params["models"], [PRIMARY_MODEL, *FALLBACK_MODELS])
        self.assertEqual(params["temperature"], 0)

    def test_missing_key_disables_llm_and_build_fails_closed(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(llm_enabled())
            with self.assertRaises(RuntimeError):
                build_llm("router")

    def test_served_model_logger_flags_fallback(self) -> None:
        handler = ServedModelLogger("synthesizer")
        result = LLMResult(
            generations=[
                [
                    ChatGeneration(
                        message=AIMessage(
                            content="ok",
                            response_metadata={"model_name": FALLBACK_MODELS[0]},
                        )
                    )
                ]
            ]
        )

        with self.assertLogs("course_discovery.app.llm", level="INFO") as logs:
            handler.on_llm_end(result)

        record = logs.records[0]
        self.assertEqual(record.served_model, FALLBACK_MODELS[0])
        self.assertTrue(record.fell_back)
        self.assertEqual(record.levelname, "WARNING")

    def test_rate_limiter_is_passed_through(self) -> None:
        limiter = InMemoryRateLimiter(requests_per_second=1)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            llm = build_llm("router", rate_limiter=limiter)

        self.assertIs(llm.rate_limiter, limiter)


if __name__ == "__main__":
    unittest.main()
