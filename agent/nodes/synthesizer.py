from __future__ import annotations

import os
import time
from typing import Any
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agent.logging_utils import get_logger, sanitize_error
from agent.models import CourseCandidate, RoutingAction
from agent.prompts import SYNTHESIZER_SYSTEM_PROMPT
from agent.state import AgentState


logger = get_logger(__name__)


def _extract_content(response: Any) -> str:
    content = getattr(response, "content", "")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(str(item) for item in content).strip()
    return str(content).strip()


def _build_synthesizer_llm() -> ChatGoogleGenerativeAI:
    if not os.getenv("GOOGLE_API_KEY"):
        raise RuntimeError("GOOGLE_API_KEY is required for synthesizer node")
    return ChatGoogleGenerativeAI(model="gemini-2.5-flash-lite", temperature=0)


def _parse_date(value: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return datetime(1970, 1, 1)


def _rank_courses(courses: list[CourseCandidate]) -> list[CourseCandidate]:
    return sorted(
        courses,
        key=lambda c: (
            c.price > 0,
            -c.rating,
            -_parse_date(c.last_updated).timestamp(),
        ),
    )


def _highlight_with_retry(
    llm: ChatGoogleGenerativeAI,
    course: CourseCandidate,
    rewrite_instructions: str | None,
    *,
    run_id: str,
    course_idx: int,
) -> str:
    payload = {
        "title": course.title,
        "provider": course.provider,
        "rating": course.rating,
        "price": course.price,
        "certificate_type": course.certificate_type,
        "description": course.description,
        "language": course.language,
        "last_updated": course.last_updated,
        "enrollment_count": course.enrollment_count,
    }

    rewrite_clause = ""
    if rewrite_instructions:
        rewrite_clause = f"\nApply this feedback: {rewrite_instructions}"

    messages = [
        SystemMessage(content=f"{SYNTHESIZER_SYSTEM_PROMPT}{rewrite_clause}"),
        HumanMessage(content=f"Summarize this course:\n{payload}"),
    ]

    call_start = time.perf_counter()
    try:
        result = _extract_content(llm.invoke(messages))
        logger.info(
            "synthesizer_llm_call",
            extra={
                "event": "synthesizer.llm_call_attempt",
                "run_id": run_id,
                "course_idx": course_idx,
                "course_title": course.title,
                "attempt": 1,
                "duration_ms": int((time.perf_counter() - call_start) * 1000),
                "rewrite_present": bool(rewrite_instructions),
            },
        )
        return result
    except Exception as first_exc:  # noqa: BLE001
        logger.warning(
            "synthesizer_retry",
            extra={
                "event": "synthesizer.llm_retry",
                "run_id": run_id,
                "course_idx": course_idx,
                "course_title": course.title,
                "attempt": 2,
                **sanitize_error(first_exc),
            },
        )
        try:
            retry_start = time.perf_counter()
            result = _extract_content(llm.invoke(messages))
            logger.info(
                "synthesizer_llm_call",
                extra={
                    "event": "synthesizer.llm_call_attempt",
                    "run_id": run_id,
                    "course_idx": course_idx,
                    "course_title": course.title,
                    "attempt": 2,
                    "duration_ms": int((time.perf_counter() - retry_start) * 1000),
                    "rewrite_present": bool(rewrite_instructions),
                },
            )
            return result
        except Exception as retry_exc:  # noqa: BLE001
            logger.error(
                "synthesizer_fallback_used",
                extra={
                    "event": "synthesizer.fallback_used",
                    "run_id": run_id,
                    "course_idx": course_idx,
                    "course_title": course.title,
                    **sanitize_error(retry_exc),
                },
            )
            return (
                f"{course.title} by {course.provider}. Rating {course.rating}/5, "
                f"price ${course.price:.2f}, certificate: {course.certificate_type}."
            )


def synthesizer_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    run_id = state.get("run_id", "unknown")
    courses = _rank_courses(state.get("deduplicated_courses", []))
    top_courses = courses[:15]
    rewrite_instructions = state.get("rewrite_instructions")

    logger.info(
        "synthesizer_start",
        extra={
            "event": "synthesizer.ranking_start",
            "run_id": run_id,
            "input_count": len(courses),
            "rewrite_present": bool(rewrite_instructions),
        },
    )

    llm = _build_synthesizer_llm()
    lines: list[str] = []

    for idx, course in enumerate(top_courses, start=1):
        highlight = _highlight_with_retry(
            llm,
            course,
            rewrite_instructions,
            run_id=run_id,
            course_idx=idx,
        )
        lines.append(
            "\n".join(
                [
                    f"{idx}. *{course.title}* ({course.provider})",
                    f"   - URL: {course.url}",
                    f"   - Price: ${course.price:.2f} | Rating: {course.rating}/5 | Certificate: {course.certificate_type}",
                    f"   - {highlight}",
                ]
            )
        )

    omitted = len(courses) - len(top_courses)
    footer = ""
    if omitted > 0:
        footer = f"\n\n_Omitted {omitted} additional courses beyond the top 15 cap._"

    digest = (
        "\n\n".join(lines)
        if lines
        else "No suitable courses found for the selected filters."
    )
    digest = f"# Course Digest\n\n{digest}{footer}"

    logger.info(
        "synthesizer_complete",
        extra={
            "event": "synthesizer.digest_complete",
            "run_id": run_id,
            "courses_rendered": len(top_courses),
            "omitted_count": omitted,
            "digest_len": len(digest),
            "duration_ms": int((time.perf_counter() - start_ts) * 1000),
        },
    )

    return {
        "digest": digest,
        "rewrite_instructions": (
            None
            if state.get("routing_decision") != RoutingAction.REWRITE
            else rewrite_instructions
        ),
    }
