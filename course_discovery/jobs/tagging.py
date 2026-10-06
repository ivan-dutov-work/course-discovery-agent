from __future__ import annotations

import hashlib
import re
from typing import Any, Protocol

from course_discovery.effects.gateway import EffectGateway
from course_discovery.effects.models import Effect
from course_discovery.effects.worker import Handler
from course_discovery.persistence.postgres import open_connection
from course_discovery.research_agent.tagging.vocabulary import TOPIC_VOCABULARY

TAG_COURSE = "tag_course"
MAX_TOPICS = 5


class TopicTagger(Protocol):
    def tag(self, title: str, description: str) -> list[str]: ...


class KeywordTagger:
    def __init__(self) -> None:
        self._patterns = {
            topic: re.compile(rf"\b{re.escape(topic.replace('-', ' '))}\b")
            for topic in TOPIC_VOCABULARY
        }

    def tag(self, title: str, description: str) -> list[str]:
        text = f"{title} {description}".lower().replace("-", " ")
        found = [topic for topic, pattern in self._patterns.items() if pattern.search(text)]
        return found[:MAX_TOPICS]


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def tagging_key(url: str, content_hash: str) -> str:
    return f"tag:{url_hash(url)}:{content_hash}"


def schedule_tagging(
    database_url: str,
    gateway: EffectGateway,
    *,
    url_prefix: str | None = None,
    limit: int | None = None,
) -> int:
    clauses = [
        "((cardinality(topics) = 0 AND tagged_content_hash IS NULL)"
        " OR (tagged_content_hash IS NOT NULL AND tagged_content_hash <> content_hash))"
    ]
    params: list[Any] = []
    if url_prefix:
        escaped = url_prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        clauses.append("canonical_url LIKE %s")
        params.append(escaped + "%")
    query = f"SELECT id, canonical_url, content_hash FROM courses WHERE {' AND '.join(clauses)} ORDER BY id"
    if limit:
        query += " LIMIT %s"
        params.append(limit)
    with open_connection(database_url) as conn:
        rows = conn.execute(query, params).fetchall()
    for course_id, url, content_hash in rows:
        gateway.submit(
            Effect(
                key=tagging_key(url, content_hash),
                kind=TAG_COURSE,
                payload={"course_id": course_id, "content_hash": content_hash},
            )
        )
    return len(rows)


def tagging_handler(database_url: str, tagger: TopicTagger) -> Handler:
    from course_discovery.jobs.tagging_graph import build_tagging_graph

    graph = build_tagging_graph(database_url, tagger)

    def handle(effect: Effect) -> None:
        graph.invoke(
            {
                "course_id": effect.payload["course_id"],
                "content_hash": effect.payload["content_hash"],
            },
            {"configurable": {"thread_id": effect.key}},
        )

    return handle
