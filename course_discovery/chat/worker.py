from __future__ import annotations

import asyncio
import os
import uuid
from typing import Awaitable, Callable

from course_discovery.chat.store import Batch, ChatStore, LeaseLost, MAX_ATTEMPTS
from course_discovery.effects.factory import get_gateway
from course_discovery.effects.handlers import SEND_CHAT_NOTICE
from course_discovery.effects.models import Effect
from course_discovery.observability.logging import get_logger, sanitize_error

logger = get_logger(__name__)

Runner = Callable[[str, str, str], Awaitable[None]]

FAILURE_NOTICE = "Something went wrong with that request. Please try again."


class ChatWorker:
    def __init__(self, store: ChatStore, runner: Runner, worker_id: str | None = None) -> None:
        self.store = store
        self.runner = runner
        self.worker_id = worker_id or f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

    async def run_once(self) -> bool:
        batch = self.store.claim(self.worker_id)
        if batch is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(batch))
        try:
            await self.runner(batch.user_id, batch.thread_id, batch.text)
        except Exception as exc:
            logger.error(
                "chat_run_failed",
                extra={"event": "chat.run_failed", "batch_id": batch.batch_id, "error": sanitize_error(exc)},
            )
            if batch.attempts < MAX_ATTEMPTS:
                return True
            self._notify_failure(batch)
        finally:
            heartbeat.cancel()
        try:
            self.store.complete(batch)
        except LeaseLost:
            logger.warning("chat_lease_lost", extra={"event": "chat.lease_lost", "batch_id": batch.batch_id})
        return True

    def _notify_failure(self, batch: Batch) -> None:
        get_gateway().submit(
            Effect(
                key=f"chat_notice:{batch.batch_id}",
                kind=SEND_CHAT_NOTICE,
                payload={"user_id": batch.user_id, "text": FAILURE_NOTICE},
            )
        )

    async def _heartbeat(self, batch: Batch) -> None:
        interval = max(self.store.lease_seconds / 3, 0.05)
        while True:
            await asyncio.sleep(interval)
            await asyncio.to_thread(self.store.renew, batch)

    async def run_until(self, stop: asyncio.Event, poll_seconds: float = 0.5) -> None:
        while not stop.is_set():
            if not await self.run_once():
                try:
                    await asyncio.wait_for(stop.wait(), poll_seconds)
                except asyncio.TimeoutError:
                    pass
