from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from typing import Protocol

REVIEW_THRESHOLD = 0.35
BLOCK_THRESHOLD = 0.70


class InjectionGuardError(RuntimeError):
    pass


class InjectionScreen(Protocol):
    def score(self, text: str) -> float: ...


class InjectionAction(str, Enum):
    PASS = "pass"
    REVIEW = "review"
    BLOCK = "block"


_override: InjectionScreen | None = None


def injection_guard_enabled() -> bool:
    return os.getenv("INJECTION_GUARD", "on").lower() not in {"off", "false", "0"}


def classify_score(score: float) -> InjectionAction:
    if score >= float(os.getenv("INJECTION_BLOCK_THRESHOLD", BLOCK_THRESHOLD)):
        return InjectionAction.BLOCK
    if score >= float(os.getenv("INJECTION_REVIEW_THRESHOLD", REVIEW_THRESHOLD)):
        return InjectionAction.REVIEW
    return InjectionAction.PASS


@lru_cache(maxsize=1)
def _default_screen() -> InjectionScreen:
    from course_discovery.guardrails.jev import JevInjectionScreen

    return JevInjectionScreen()


def set_injection_screen(screen: InjectionScreen | None) -> None:
    global _override
    _override = screen


def get_injection_screen() -> InjectionScreen:
    return _override or _default_screen()


def _record_degradation(reason: str, exc: BaseException | None = None) -> None:
    from course_discovery.observability.logging import get_logger, sanitize_error
    from course_discovery.observability.metrics import record_degradation

    record_degradation("injection_guard", reason)
    if exc is not None:
        get_logger(__name__).warning(
            "injection_screen_failed",
            extra={"event": "guardrail.injection_screen_failed", **sanitize_error(exc)},
        )


def screen_untrusted(text: str) -> InjectionAction:
    if not injection_guard_enabled() or not text.strip():
        return InjectionAction.PASS
    try:
        action = classify_score(get_injection_screen().score(text))
    except Exception as exc:  # noqa: BLE001
        _record_degradation("screen_failed", exc)
        return InjectionAction.REVIEW
    if action is not InjectionAction.PASS:
        _record_degradation(action.value)
    return action
