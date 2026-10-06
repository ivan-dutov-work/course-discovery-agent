from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Protocol

MAX_MESSAGE_CHARS = 4096


@dataclass(frozen=True)
class InboundMessage:
    update_id: int
    chat_id: int
    text: str


class ChatTransport(Protocol):
    def parse_update(self, payload: dict[str, Any]) -> InboundMessage | None: ...

    def send(self, chat_id: int, text: str) -> None: ...


def parse_telegram_update(payload: dict[str, Any]) -> InboundMessage | None:
    message = payload.get("message")
    update_id = payload.get("update_id")
    if not isinstance(update_id, int) or not isinstance(message, dict):
        return None
    text = message.get("text")
    chat_id = (message.get("chat") or {}).get("id")
    if not isinstance(text, str) or not text.strip() or not isinstance(chat_id, int):
        return None
    return InboundMessage(update_id=update_id, chat_id=chat_id, text=text)


def split_message(text: str, limit: int = MAX_MESSAGE_CHARS) -> list[str]:
    return [text[i : i + limit] for i in range(0, len(text), limit)] or [""]


@dataclass
class FakeTransport:
    sent: list[tuple[int, str]] = field(default_factory=list)

    def parse_update(self, payload: dict[str, Any]) -> InboundMessage | None:
        return parse_telegram_update(payload)

    def send(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


class TelegramTransport:
    def __init__(self, token: str, api_base: str = "https://api.telegram.org", timeout: float = 10.0):
        self._url = f"{api_base.rstrip('/')}/bot{token}/sendMessage"
        self._timeout = timeout

    def parse_update(self, payload: dict[str, Any]) -> InboundMessage | None:
        return parse_telegram_update(payload)

    def send(self, chat_id: int, text: str) -> None:
        for part in split_message(text):
            request = urllib.request.Request(
                self._url,
                data=json.dumps({"chat_id": chat_id, "text": part}).encode(),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                response.read()
