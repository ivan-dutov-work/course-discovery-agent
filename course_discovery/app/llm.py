from __future__ import annotations

import os
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from langchain_core.rate_limiters import BaseRateLimiter
from langchain_openrouter import ChatOpenRouter

from course_discovery.observability.logging import get_logger
from course_discovery.resilience import LLM_TIMEOUT_MS

PRIMARY_MODEL = "deepseek/deepseek-v4.1-flash"
FALLBACK_MODELS = ["google/gemini-2.5-flash-lite"]

logger = get_logger(__name__)


class ServedModelLogger(BaseCallbackHandler):
    def __init__(self, node: str) -> None:
        self.node = node

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        message = getattr(response.generations[0][0], "message", None)
        served = (getattr(message, "response_metadata", None) or {}).get("model_name")
        fell_back = bool(served) and served != PRIMARY_MODEL
        logger.log(
            30 if fell_back else 20,
            "llm_call_served",
            extra={
                "event": "llm.served",
                "node": self.node,
                "requested_model": PRIMARY_MODEL,
                "served_model": served,
                "fell_back": fell_back,
            },
        )


def llm_enabled() -> bool:
    return bool(os.getenv("OPENROUTER_API_KEY"))


def build_llm(
    node: str,
    *,
    rate_limiter: BaseRateLimiter | None = None,
    max_retries: int = 0,
) -> ChatOpenRouter:
    if not llm_enabled():
        raise RuntimeError(f"OPENROUTER_API_KEY is required for {node} node")
    return ChatOpenRouter(
        model=PRIMARY_MODEL,
        temperature=0,
        model_kwargs={"models": [PRIMARY_MODEL, *FALLBACK_MODELS]},
        rate_limiter=rate_limiter,
        request_timeout=LLM_TIMEOUT_MS,
        max_retries=max_retries,
        callbacks=[ServedModelLogger(node)],
    )
