from __future__ import annotations

import os
import time
from typing import Any
from uuid import UUID

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.rate_limiters import BaseRateLimiter
from langchain_openrouter import ChatOpenRouter

from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.observability.metrics import record_llm_call, record_llm_error
from course_discovery.resilience import LLM_TIMEOUT_MS

PRIMARY_MODEL = "deepseek/deepseek-v4.1-flash"
FALLBACK_MODELS = ["google/gemini-2.5-flash-lite"]
TAGGER_MODEL = "openai/gpt-6-luna"
JUDGE_MODEL = "google/gemini-3.1-flash-lite"

logger = get_logger(__name__)


class ServedModelLogger(BaseCallbackHandler):
    def __init__(self, node: str, requested_model: str = PRIMARY_MODEL) -> None:
        self.node = node
        self.requested_model = requested_model
        self._starts: dict[UUID, float] = {}

    def on_chat_model_start(self, serialized: Any, messages: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts[run_id] = time.perf_counter()

    def on_llm_start(self, serialized: Any, prompts: Any, *, run_id: UUID, **kwargs: Any) -> None:
        self._starts[run_id] = time.perf_counter()

    def _elapsed(self, run_id: UUID | None) -> float | None:
        started = self._starts.pop(run_id, None) if run_id is not None else None
        return None if started is None else time.perf_counter() - started

    def on_llm_error(
        self, error: BaseException, *, run_id: UUID | None = None, **kwargs: Any
    ) -> None:
        record_llm_error(
            self.node,
            requested_model=self.requested_model,
            error_type=type(error).__name__,
            seconds=self._elapsed(run_id),
        )
        logger.warning(
            "llm_call_failed",
            extra={
                "event": "llm.failed",
                "node": self.node,
                "requested_model": self.requested_model,
                **sanitize_error(error),
            },
        )

    def on_llm_end(
        self, response: LLMResult, *, run_id: UUID | None = None, **kwargs: Any
    ) -> None:
        message = getattr(response.generations[0][0], "message", None)
        served = (getattr(message, "response_metadata", None) or {}).get("model_name")
        usage = getattr(message, "usage_metadata", None) or {}
        fell_back = bool(served) and not served.startswith(self.requested_model)
        record_llm_call(
            self.node,
            requested_model=self.requested_model,
            served_model=served,
            fell_back=fell_back,
            seconds=self._elapsed(run_id),
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )
        logger.log(
            30 if fell_back or not served else 20,
            "llm_call_served",
            extra={
                "event": "llm.served",
                "node": self.node,
                "requested_model": self.requested_model,
                "served_model": served,
                "fell_back": fell_back,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cached_tokens": (usage.get("input_token_details") or {}).get("cache_read"),
            },
        )


def llm_enabled() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def build_llm(
    node: str,
    *,
    model: str = PRIMARY_MODEL,
    fallback_models: list[str] | None = None,
    rate_limiter: BaseRateLimiter | None = None,
    max_retries: int = 0,
) -> ChatOpenRouter:
    if not llm_enabled():
        raise RuntimeError(f"OPENROUTER_API_KEY is required for {node} node")
    fallbacks = FALLBACK_MODELS if fallback_models is None else fallback_models
    return ChatOpenRouter(
        model=model,
        temperature=0,
        model_kwargs={"models": [model, *fallbacks]},
        rate_limiter=rate_limiter,
        request_timeout=LLM_TIMEOUT_MS,
        max_retries=max_retries,
        callbacks=[ServedModelLogger(node, model)],
    )
