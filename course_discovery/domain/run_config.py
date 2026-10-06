from __future__ import annotations

from langchain_core.runnables import RunnableConfig
from langgraph.config import get_config

DEFAULT_MAX_REVIEW_ROUNDS = 3
DEFAULT_MAX_RESEARCH_ITERATIONS = 2


def _configurable(config: RunnableConfig | None) -> dict:
    if config is None:
        try:
            config = get_config()
        except RuntimeError:
            return {}
    return config.get("configurable") or {}


def run_id_of(config: RunnableConfig) -> str:
    return config["configurable"]["thread_id"]


def current_run_id() -> str:
    return _configurable(None).get("thread_id") or "unknown"


def max_review_rounds(config: RunnableConfig | None = None) -> int:
    return _configurable(config).get("max_review_rounds", DEFAULT_MAX_REVIEW_ROUNDS)


def max_research_iterations(config: RunnableConfig | None = None) -> int:
    return _configurable(config).get("max_research_iterations", DEFAULT_MAX_RESEARCH_ITERATIONS)


def chat_mode(config: RunnableConfig | None = None) -> bool:
    return bool(_configurable(config).get("chat_mode", False))


def close_reason(config: RunnableConfig | None = None) -> str | None:
    return _configurable(config).get("close_reason")
