from __future__ import annotations

import os
from functools import lru_cache

from pii_redaction import PresidioRedactor, Redactor


class PiiGuardrailError(RuntimeError):
    pass


class _PassthroughRedactor:
    def redact(self, text: str) -> str:
        return text


_override: Redactor | None = None


def pii_guardrail_enabled() -> bool:
    return os.getenv("PII_GUARDRAIL", "on").lower() not in {"off", "false", "0"}


@lru_cache(maxsize=1)
def _default_redactor() -> Redactor:
    return PresidioRedactor()


def set_redactor(redactor: Redactor | None) -> None:
    global _override
    _override = redactor


def get_redactor() -> Redactor:
    if not pii_guardrail_enabled():
        return _PassthroughRedactor()
    return _override or _default_redactor()


def redact_pii(text: str | None) -> str | None:
    if not text:
        return text
    try:
        return get_redactor().redact(text)
    except Exception as exc:  # noqa: BLE001
        raise PiiGuardrailError(f"PII redaction failed: {type(exc).__name__}") from exc
