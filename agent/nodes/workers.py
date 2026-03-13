from __future__ import annotations

import asyncio
import time
from urllib.parse import urlparse

from agent.logging_utils import get_logger
from agent.models import CourseCandidate
from agent.state import AgentState


logger = get_logger(__name__)


def _course(
    *,
    title: str,
    url: str,
    provider: str,
    description: str,
    price: float,
    rating: float,
    certificate_type: str,
    source_worker: str,
    language: str,
    last_updated: str,
    enrollment_count: int,
) -> CourseCandidate:
    parsed = urlparse(url)
    if not parsed.scheme:
        raise ValueError(f"Invalid URL for mock course: {url}")
    return CourseCandidate(
        title=title,
        url=url,
        provider=provider,
        description=description,
        price=price,
        rating=rating,
        certificate_type=certificate_type,
        source_worker=source_worker,
        language=language,
        last_updated=last_updated,
        enrollment_count=enrollment_count,
    )


async def worker_a_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    run_id = state.get("run_id", "unknown")
    logger.info(
        "worker_start",
        extra={"event": "worker.start", "run_id": run_id, "worker": "worker_a"},
    )

    await asyncio.sleep(1.5)
    courses = [
        _course(
            title="Python for Everybody",
            url="https://www.coursera.org/specializations/python",
            provider="coursera",
            description="Beginner-friendly Python specialization with assignments.",
            price=0.0,
            rating=4.8,
            certificate_type="paid_optional",
            source_worker="worker_a",
            language="en",
            last_updated="2025-11-15",
            enrollment_count=520000,
        ),
        _course(
            title="Python OOP in 100 Minutes",
            url="https://www.youtube.com/watch?v=JeznW_7DlB0",
            provider="youtube",
            description="Fast practical walkthrough of OOP basics in Python.",
            price=0.0,
            rating=4.6,
            certificate_type="none",
            source_worker="worker_a",
            language="en",
            last_updated="2025-08-21",
            enrollment_count=120000,
        ),
    ]
    logger.info(
        "worker_complete",
        extra={
            "event": "worker.complete",
            "run_id": run_id,
            "worker": "worker_a",
            "course_count": len(courses),
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
        },
    )
    return {"worker_a_courses": courses}


async def worker_b_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    run_id = state.get("run_id", "unknown")
    logger.info(
        "worker_start",
        extra={"event": "worker.start", "run_id": run_id, "worker": "worker_b"},
    )

    await asyncio.sleep(1.5)
    courses = [
        _course(
            title="Google IT Automation with Python",
            url="https://www.coursera.org/professional-certificates/google-it-automation",
            provider="coursera",
            description="Automation-focused Python program with career-oriented modules.",
            price=0.0,
            rating=4.7,
            certificate_type="paid_optional",
            source_worker="worker_b",
            language="en",
            last_updated="2025-10-10",
            enrollment_count=210000,
        ),
        _course(
            title="Python for Everybody",
            url="https://www.coursera.org/specializations/python?utm_source=reddit",
            provider="coursera",
            description="Duplicate candidate for dedup testing.",
            price=0.0,
            rating=4.8,
            certificate_type="paid_optional",
            source_worker="worker_b",
            language="en",
            last_updated="2025-09-05",
            enrollment_count=520000,
        ),
    ]
    logger.info(
        "worker_complete",
        extra={
            "event": "worker.complete",
            "run_id": run_id,
            "worker": "worker_b",
            "course_count": len(courses),
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
        },
    )
    return {"worker_b_courses": courses}


async def worker_c_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    run_id = state.get("run_id", "unknown")
    logger.info(
        "worker_start",
        extra={"event": "worker.start", "run_id": run_id, "worker": "worker_c"},
    )

    await asyncio.sleep(1.5)
    courses = [
        _course(
            title="CS50's Introduction to Programming with Python",
            url="https://www.edx.org/learn/python/harvard-university-cs50-s-introduction-to-programming-with-python",
            provider="edx",
            description="Rigorous intro to Python with problem sets and lectures.",
            price=0.0,
            rating=4.9,
            certificate_type="paid_optional",
            source_worker="worker_c",
            language="en",
            last_updated="2025-12-01",
            enrollment_count=185000,
        ),
        _course(
            title="Python OOP in 100 minutes",
            url="https://www.youtube.com/watch?v=JeznW_7DlB0/",
            provider="youtube",
            description="Near-duplicate title with trailing slash URL variant.",
            price=0.0,
            rating=4.5,
            certificate_type="none",
            source_worker="worker_c",
            language="en",
            last_updated="2025-07-04",
            enrollment_count=120000,
        ),
    ]
    logger.info(
        "worker_complete",
        extra={
            "event": "worker.complete",
            "run_id": run_id,
            "worker": "worker_c",
            "course_count": len(courses),
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
        },
    )
    return {"worker_c_courses": courses}


def aggregate_node(state: AgentState) -> dict:
    run_id = state.get("run_id", "unknown")
    existing = state.get("scraped_courses", [])
    from_workers = (
        state.get("worker_a_courses", [])
        + state.get("worker_b_courses", [])
        + state.get("worker_c_courses", [])
    )

    seen_urls: set[str] = set()
    merged: list[CourseCandidate] = []
    for course in existing + from_workers:
        url_key = str(course.url).rstrip("/")
        if url_key in seen_urls:
            continue
        seen_urls.add(url_key)
        merged.append(course)

    capped = merged[:200]
    logger.info(
        "aggregate_complete",
        extra={
            "event": "aggregate.complete",
            "run_id": run_id,
            "existing_count": len(existing),
            "new_worker_count": len(from_workers),
            "merged_count": len(merged),
            "capped_count": len(capped),
            "cap_applied": len(merged) > 200,
        },
    )

    return {"scraped_courses": capped}
