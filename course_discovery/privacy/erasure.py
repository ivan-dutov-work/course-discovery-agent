from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver

from course_discovery.observability.logging import get_logger
from course_discovery.persistence.postgres import connect
from course_discovery.privacy.sources import USER_DATA_SOURCES


logger = get_logger(__name__)


@dataclass
class ErasureReport:
    user_ref: str
    executed: bool
    threads: list[str] = field(default_factory=list)
    rows: dict[str, int] = field(default_factory=dict)
    outbox_by_status: dict[str, int] = field(default_factory=dict)
    residual: dict[str, int] = field(default_factory=dict)

    @property
    def clean(self) -> bool:
        return self.executed and not any(self.residual.values())


def user_ref(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()[:12]


def _thread_ids(conn: Any, user_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT thread_id FROM run_threads WHERE user_id = %s ORDER BY created_at", (user_id,)
    )
    return [row[0] for row in rows]


def _survey(conn: Any, user_id: str) -> tuple[dict[str, int], dict[str, int]]:
    rows: dict[str, int] = {}
    outbox_by_status: dict[str, int] = {}
    for source in USER_DATA_SOURCES:
        rows[source.table], breakdown = source.count(conn, user_id)
        if source.table == "outbox":
            outbox_by_status = breakdown
    return rows, outbox_by_status


async def _surviving_threads(saver: BaseCheckpointSaver, thread_ids: list[str]) -> int:
    surviving = 0
    for thread_id in thread_ids:
        try:
            async for _ in saver.alist({"configurable": {"thread_id": thread_id}}, limit=1):
                surviving += 1
        except Exception:  # noqa: BLE001
            surviving += 1
    return surviving


async def erase_user(
    user_id: str, saver: BaseCheckpointSaver, *, execute: bool = False
) -> ErasureReport:
    with connect() as conn:
        if conn is None:
            raise RuntimeError("DATABASE_URL is not set; nothing to erase")
        threads = _thread_ids(conn, user_id)
        rows, outbox_by_status = _survey(conn, user_id)

    report = ErasureReport(
        user_ref=user_ref(user_id),
        executed=execute,
        threads=threads,
        rows=rows,
        outbox_by_status=outbox_by_status,
    )
    if not execute:
        return report

    for thread_id in threads:
        await saver.adelete_thread(thread_id)

    with connect() as conn:
        with conn.transaction():
            for source in USER_DATA_SOURCES:
                source.delete(conn, user_id)
        report.residual, _ = _survey(conn, user_id)
    report.residual["checkpoint_threads"] = await _surviving_threads(saver, threads)

    logger.info(
        "user_erased",
        extra={
            "event": "privacy.user_erased",
            "user_ref": report.user_ref,
            "threads": len(threads),
            "clean": report.clean,
        },
    )
    return report
