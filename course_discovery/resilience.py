from __future__ import annotations

import os

import httpx
import psycopg
from langgraph.types import RetryPolicy

LLM_TIMEOUT_MS = int(float(os.getenv("LLM_TIMEOUT_SECONDS", "30")) * 1000)
SEARCH_TIMEOUT_SECONDS = float(os.getenv("SEARCH_TIMEOUT_SECONDS", "10"))
DB_CONNECT_TIMEOUT_SECONDS = int(os.getenv("DB_CONNECT_TIMEOUT_SECONDS", "5"))
DB_STATEMENT_TIMEOUT_MS = int(float(os.getenv("DB_STATEMENT_TIMEOUT_SECONDS", "15")) * 1000)

RETRY_SETTINGS: dict = {
    "max_attempts": 3,
    "initial_interval": 0.5,
    "backoff_factor": 2.0,
    "max_interval": 8.0,
    "jitter": True,
}

_RETRYABLE_STATUS = {408, 425, 429}


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, httpx.TransportError)):
        return True
    if isinstance(exc, psycopg.OperationalError):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status in _RETRYABLE_STATUS or status >= 500)


def transient_retry() -> RetryPolicy:
    return RetryPolicy(**RETRY_SETTINGS, retry_on=is_transient)
