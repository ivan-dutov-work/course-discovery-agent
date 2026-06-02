from __future__ import annotations

import hashlib
import time
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from rapidfuzz import fuzz

from course_discovery.domain.models import CourseCandidate
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger


logger = get_logger(__name__)


def _normalize_url(url: str) -> str:
    parsed = urlparse(url)
    query_items = parse_qsl(parsed.query, keep_blank_values=True)
    filtered = [(k, v) for (k, v) in query_items if not k.lower().startswith("utm_")]
    rebuilt_query = urlencode(filtered)
    normalized_path = parsed.path.rstrip("/")
    rebuilt = parsed._replace(query=rebuilt_query, path=normalized_path)
    return urlunparse(rebuilt)


def _fingerprint(title: str, url: str) -> str:
    host = urlparse(url).hostname or ""
    payload = f"{title.strip().lower()}::{host.lower()}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def dedup_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    run_id = state.get("run_id", "unknown")
    deduplicated: list[CourseCandidate] = []
    seen_urls: set[str] = set()
    seen_fingerprints: set[str] = set()
    removed_by_url = 0
    removed_by_fingerprint = 0
    removed_by_fuzzy = 0

    input_count = len(state.get("scraped_courses", []))
    logger.info(
        "dedup_start",
        extra={"event": "dedup.start", "run_id": run_id, "input_count": input_count},
    )

    for candidate in state.get("scraped_courses", []):
        normalized_url = _normalize_url(str(candidate.url))
        title_key = candidate.title.strip().lower()
        fp = _fingerprint(title_key, normalized_url)

        if normalized_url in seen_urls:
            removed_by_url += 1
            logger.debug(
                "dedup_duplicate_url",
                extra={
                    "event": "dedup.duplicate_detected",
                    "run_id": run_id,
                    "reason": "url_normalized",
                    "title": candidate.title,
                    "url": normalized_url,
                },
            )
            continue

        if fp in seen_fingerprints:
            removed_by_fingerprint += 1
            logger.debug(
                "dedup_duplicate_fingerprint",
                extra={
                    "event": "dedup.duplicate_detected",
                    "run_id": run_id,
                    "reason": "fingerprint",
                    "title": candidate.title,
                },
            )
            continue

        is_fuzzy_duplicate = any(
            fuzz.ratio(title_key, prev.title.strip().lower()) >= 90
            for prev in deduplicated
        )
        if is_fuzzy_duplicate:
            removed_by_fuzzy += 1
            logger.debug(
                "dedup_duplicate_fuzzy",
                extra={
                    "event": "dedup.duplicate_detected",
                    "run_id": run_id,
                    "reason": "fuzzy_title",
                    "title": candidate.title,
                },
            )
            continue

        seen_urls.add(normalized_url)
        seen_fingerprints.add(fp)
        deduplicated.append(candidate)

    output_count = len(deduplicated)
    logger.info(
        "dedup_complete",
        extra={
            "event": "dedup.complete",
            "run_id": run_id,
            "input_count": input_count,
            "output_count": output_count,
            "removed_by_url": removed_by_url,
            "removed_by_fingerprint": removed_by_fingerprint,
            "removed_by_fuzzy": removed_by_fuzzy,
            "effective_dedup_rate_pct": round(
                (
                    ((input_count - output_count) / input_count * 100)
                    if input_count
                    else 0.0
                ),
                2,
            ),
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
        },
    )

    return {"deduplicated_courses": deduplicated}
