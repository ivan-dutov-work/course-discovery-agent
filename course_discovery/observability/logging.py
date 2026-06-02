from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any


_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_\-]{12,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9_\-\.]{12,}", re.IGNORECASE),
]


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter for structured logs."""

    _BASE_ATTRS = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in self._BASE_ATTRS:
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=True, default=str)


def configure_logging() -> None:
    """Configure root logging once, defaulting to JSON lines output."""
    root = logging.getLogger()
    if getattr(root, "_course_agent_logging_configured", False):
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root._course_agent_logging_configured = True  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def mask_secrets(value: str) -> str:
    masked = value
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub("[REDACTED]", masked)
    return masked


def truncate_text(value: str | None, *, max_len: int = 120) -> str | None:
    if value is None:
        return None
    cleaned = mask_secrets(value)
    if len(cleaned) <= max_len:
        return cleaned
    return f"{cleaned[:max_len]}..."


def classify_feedback(feedback: str) -> str:
    lower = feedback.strip().lower()
    if lower in {"approve", "approved", "publish", "looks good"}:
        return "approve"
    if lower.startswith("rewrite"):
        return "rewrite"
    if lower.startswith("augment"):
        return "augment"
    if lower.startswith("reset"):
        return "reset"
    if lower.startswith("discard"):
        return "discard"
    return "freeform"


def sanitize_error(exc: Exception) -> dict[str, str]:
    return {
        "error_type": type(exc).__name__,
        "error_message": truncate_text(str(exc), max_len=240) or "",
    }
