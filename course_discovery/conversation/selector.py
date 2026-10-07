from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Protocol

import httpx

from course_discovery.domain.models import SearchFilters
from course_discovery.guardrails import redact_pii
from course_discovery.guardrails.jev import DEFAULT_MODEL, DEFAULT_URL, ask_decision
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.observability.metrics import record_degradation

COMPONENT = "thread_selector"
QUESTION = "continues_parked_thread"
CONTINUE_THRESHOLD = 0.70
NEW_TOPIC_THRESHOLD = 0.35
SIGNAL_QUESTION = "is_acknowledgement_only"
NO_SIGNAL_THRESHOLD = 0.70

DEPLOYMENT = (
    "A course discovery assistant chats with one person. The person has a parked course search "
    "(a topic and filters, shown in parked_thread) and has just sent new_message. The answer "
    "decides whether new_message refines that search or starts an unrelated one."
)

INSTRUCTIONS = (
    "Does new_message depend on the parked course search in parked_thread? It does when it refines "
    "or adjusts the search, asks about its results, or changes its topic or audience while "
    "keeping the other constraints, so that the answer makes sense only given the parked search. "
    "Answer false when it stands on its own as a request for courses on a different subject."
)

CRITERIA = {
    "true": (
        "The message refines, narrows, widens, changes or comments on the parked search, for "
        "example cheaper, shorter, only beginner level, no Skillshare, more like the second one, "
        "the same thing for Tableau, or something suitable for teenagers."
    ),
    "false": (
        "The message is a complete request about a different subject that needs nothing from "
        "the parked search, or is not about courses at all."
    ),
}

SIGNAL_INSTRUCTIONS = (
    "Does new_message only acknowledge, thank or close the conversation, without saying anything "
    "about which courses the person wants? Answer false when it names a topic, a filter, a "
    "preference or a question about courses, however short."
)

SIGNAL_CRITERIA = {
    "true": (
        "The message is a pleasantry or acknowledgement such as ok, cool, thanks, perfect, "
        "that is all, and gives no information about which courses the person wants."
    ),
    "false": (
        "The message asks for, narrows, widens or changes the courses wanted, or asks about "
        "the results."
    ),
}


class Decision(str, Enum):
    CONTINUE = "continue"
    NEW_TOPIC = "new_topic"
    NO_SIGNAL = "no_signal"


@dataclass(frozen=True)
class ParkedThread:
    topic: str
    filters: dict[str, Any] = field(default_factory=dict)
    age_seconds: float = 0.0

    @classmethod
    def from_filters(
        cls, filters: SearchFilters, last_activity_at: datetime, now: datetime
    ) -> ParkedThread:
        defaults = SearchFilters()
        changed = {
            name: value
            for name, value in filters.model_dump(exclude={"topic"}).items()
            if value != getattr(defaults, name)
        }
        return cls(
            topic=filters.topic,
            filters=changed,
            age_seconds=max(0.0, (now - last_activity_at).total_seconds()),
        )


@dataclass(frozen=True)
class SelectorResult:
    decision: Decision
    score: float | None = None
    reason: str = "scored"


class ThreadSelector(Protocol):
    def select(self, message: str, parked: ParkedThread | None) -> SelectorResult: ...


class StubThreadSelector:
    def __init__(self, decision: Decision = Decision.NEW_TOPIC):
        self._decision = decision
        self.seen: list[tuple[str, ParkedThread | None]] = []

    def select(self, message: str, parked: ParkedThread | None) -> SelectorResult:
        self.seen.append((message, parked))
        return SelectorResult(self._decision, reason="stub")


def _describe_age(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} minutes"
    if seconds < 86400:
        return f"{int(seconds // 3600)} hours"
    return f"{int(seconds // 86400)} days"


class JevThreadSelector:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
        continue_threshold: float | None = None,
        new_topic_threshold: float | None = None,
        no_signal_threshold: float | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY") or ""
        self.model = model or os.getenv("THREAD_SELECTOR_MODEL", DEFAULT_MODEL)
        self._url = url or os.getenv("THREAD_SELECTOR_URL", DEFAULT_URL)
        self._client = client or httpx.Client(
            timeout=timeout or float(os.getenv("THREAD_SELECTOR_TIMEOUT_SECONDS", "5"))
        )
        self.continue_threshold = continue_threshold or float(
            os.getenv("THREAD_SELECTOR_CONTINUE_THRESHOLD", CONTINUE_THRESHOLD)
        )
        self.new_topic_threshold = new_topic_threshold or float(
            os.getenv("THREAD_SELECTOR_NEW_TOPIC_THRESHOLD", NEW_TOPIC_THRESHOLD)
        )
        self.no_signal_threshold = no_signal_threshold or float(
            os.getenv("THREAD_SELECTOR_NO_SIGNAL_THRESHOLD", NO_SIGNAL_THRESHOLD)
        )

    def select(self, message: str, parked: ParkedThread | None) -> SelectorResult:
        if parked is None or not message.strip():
            return SelectorResult(Decision.NEW_TOPIC, reason="no_parked_thread")
        try:
            signal = self.signal_score(message, parked)
            if signal >= self.no_signal_threshold:
                return SelectorResult(Decision.NO_SIGNAL, signal)
            score = self.score(message, parked)
        except Exception as exc:  # noqa: BLE001
            return self._degraded("selector_failed", exc)
        if score >= self.continue_threshold:
            return SelectorResult(Decision.CONTINUE, score)
        if score < self.new_topic_threshold:
            return SelectorResult(Decision.NEW_TOPIC, score)
        return self._degraded("middle_score", score=score)

    def score(self, message: str, parked: ParkedThread) -> float:
        return self._ask(message, parked, QUESTION, INSTRUCTIONS, CRITERIA)

    def signal_score(self, message: str, parked: ParkedThread) -> float:
        return self._ask(message, parked, SIGNAL_QUESTION, SIGNAL_INSTRUCTIONS, SIGNAL_CRITERIA)

    def _ask(
        self,
        message: str,
        parked: ParkedThread,
        question: str,
        instructions: str,
        criteria: dict[str, str],
    ) -> float:
        if not self._api_key:
            raise RuntimeError("OPENROUTER_API_KEY is required for the thread selector")
        state = {
            "deployment": DEPLOYMENT,
            "parked_thread": _render_thread(parked),
            "new_message": redact_pii(message) or "",
        }
        return ask_decision(
            self._client,
            self._url,
            self._api_key,
            self.model,
            state=state,
            question=question,
            instructions=instructions,
            criteria=criteria,
        )

    def _degraded(
        self, reason: str, exc: BaseException | None = None, score: float | None = None
    ) -> SelectorResult:
        record_degradation(COMPONENT, reason)
        if exc is not None:
            get_logger(__name__).warning(
                "thread_selector_failed",
                extra={"event": "conversation.thread_selector_failed", **sanitize_error(exc)},
            )
        return SelectorResult(Decision.NEW_TOPIC, score, reason)


def _render_thread(parked: ParkedThread) -> str:
    lines = [f"topic: {redact_pii(parked.topic)}", f"last activity: {_describe_age(parked.age_seconds)} ago"]
    lines += [f"{name}: {value}" for name, value in sorted(parked.filters.items())]
    return "\n".join(lines)
