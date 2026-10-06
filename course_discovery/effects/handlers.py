from __future__ import annotations

import os

from course_discovery.effects.models import Effect, PermanentEffectError
from course_discovery.effects.worker import Handler

PUBLISH_DIGEST = "publish_digest"
SEND_DIGEST_MESSAGE = "send_digest_message"
SEND_FEEDBACK_PROMPT = "send_feedback_prompt"
SEND_CHAT_NOTICE = "send_chat_notice"


def deliver_digest(effect: Effect) -> None:
    digest = effect.payload.get("digest")
    if not digest:
        raise PermanentEffectError("publish_digest payload has no digest")
    print(f"\n=== PUBLISH (stdout stub, idempotency-key={effect.key}) ===")
    print(digest)
    print("=== END PUBLISH ===\n")


def deliver_chat_message(effect: Effect) -> None:
    text = effect.payload.get("text")
    if not text:
        raise PermanentEffectError(f"{effect.kind} payload has no text")
    print(f"\n=== CHAT MESSAGE (stdout stub) ===\n{text}\n=== END CHAT MESSAGE ===\n")


def default_handlers() -> dict[str, Handler]:
    handlers = _stub_handlers()
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        from course_discovery.jobs.tagging import TAG_COURSE, KeywordTagger, tagging_handler

        handlers[TAG_COURSE] = tagging_handler(database_url, KeywordTagger())
    return handlers


def _stub_handlers() -> dict[str, Handler]:
    return {
        PUBLISH_DIGEST: deliver_digest,
        SEND_DIGEST_MESSAGE: deliver_chat_message,
        SEND_FEEDBACK_PROMPT: deliver_chat_message,
        SEND_CHAT_NOTICE: deliver_chat_message,
    }
