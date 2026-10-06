from __future__ import annotations

import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from course_discovery.chat.store import ChatStore
from course_discovery.chat.transport import ChatTransport
from course_discovery.guardrails import redact_pii
from course_discovery.observability.logging import get_logger, sanitize_error

logger = get_logger(__name__)

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


class ChatIntake:
    def __init__(self, store: ChatStore, transport: ChatTransport) -> None:
        self.store = store
        self.transport = transport

    def handle(self, payload: dict) -> bool:
        message = self.transport.parse_update(payload)
        if message is None:
            return False
        return self.store.ingest(message, redact_pii(message.text) or "")


def make_server(
    intake: ChatIntake, host: str = "127.0.0.1", port: int = 0, shared_key: str | None = None
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            if self.path != "/webhook":
                return self._reply(404)
            if shared_key and not hmac.compare_digest(self.headers.get(SECRET_HEADER, ""), shared_key):
                return self._reply(403)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                intake.handle(json.loads(self.rfile.read(length)))
            except ValueError:
                return self._reply(400)
            except Exception as exc:
                logger.error(
                    "chat_ingest_failed",
                    extra={"event": "chat.ingest_failed", "error": sanitize_error(exc)},
                )
                return self._reply(500)
            self._reply(200)

        def _reply(self, status: int) -> None:
            self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *args) -> None:
            pass

    return ThreadingHTTPServer((host, port), Handler)


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread
