from __future__ import annotations

from course_discovery.chat.store import ChatStore
from course_discovery.chat.transport import ChatTransport
from course_discovery.effects.handlers import (
    SEND_CHAT_NOTICE,
    SEND_DIGEST_MESSAGE,
    SEND_FEEDBACK_PROMPT,
)
from course_discovery.effects.models import Effect, PermanentEffectError
from course_discovery.effects.worker import Handler


def chat_handlers(store: ChatStore, transport: ChatTransport) -> dict[str, Handler]:
    def deliver(effect: Effect) -> None:
        text = effect.payload.get("text")
        if not text:
            raise PermanentEffectError(f"{effect.kind} payload has no text")
        chat_id = store.chat_id_for(effect.payload.get("user_id") or "")
        if chat_id is None:
            raise PermanentEffectError(f"{effect.kind} has no chat for its user")
        transport.send(chat_id, text)

    return {
        SEND_DIGEST_MESSAGE: deliver,
        SEND_FEEDBACK_PROMPT: deliver,
        SEND_CHAT_NOTICE: deliver,
    }
