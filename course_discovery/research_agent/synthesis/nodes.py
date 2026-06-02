from __future__ import annotations

import os
import time
from typing import Any
from datetime import datetime

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from course_discovery.app.prompts import SYNTHESIZER_SYSTEM_PROMPT
from course_discovery.domain.models import CourseCandidate, RoutingAction
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger, sanitize_error


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
            c.is_free is not True,
            -(c.rating or 0),
            -_parse_date(c.published_or_updated or "").timestamp(),
            -c.confidence,
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
        "is_free": course.is_free,
        "has_certificate": course.has_certificate,
        "description": course.description,
        "language": course.language,
        "published_or_updated": course.published_or_updated,
        "evidence": [item.model_dump() for item in course.evidence],
    }

    rewrite_clause = ""
    if rewrite_instructions:
        rewrite_clause = f"\nApply this feedback: {rewrite_instructions}"

    messages = [
        SystemMessage(content=f"{SYNTHESIZER_SYSTEM_PROMPT}{rewrite_clause}"),
        HumanMessage(content=f"Summarize this course:\n{payload}"),
    ]

    if not os.getenv("GOOGLE_API_KEY"):
        evidence = "; ".join(item.quote_or_summary for item in course.evidence[:2])
        return (
            f"Fits the request based on validated evidence for "
            f"{', '.join(course.evidence[0].supports) if course.evidence else 'course facts'}. "
            f"{evidence}"
        ).strip()

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
                f"{course.title} by {course.provider or 'unknown provider'}. "
                f"Price: {course.price or 'unknown'}, certificate evidence: {course.has_certificate}."
            )


def synthesizer_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    run_id = state.get("run_id", "unknown")
    courses = _rank_courses(state.get("valid_courses", []))
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

    llm = _build_synthesizer_llm() if os.getenv("GOOGLE_API_KEY") else None
    lines: list[str] = []
    validation_by_url = {item.url: item for item in state.get("validation_results", [])}

    for idx, course in enumerate(top_courses, start=1):
        highlight = _highlight_with_retry(
            llm,  # type: ignore[arg-type]
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
                    f"   - Price: {course.price or 'unknown'} | Rating: {course.rating or 'unknown'} | Certificate: {course.has_certificate}",
                    f"   - Evidence: {validation_by_url.get(course.url).reasons[0] if validation_by_url.get(course.url) else 'validated'}",
                    f"   - {highlight}",
                ]
            )
        )

    omitted = len(courses) - len(top_courses)
    footer = ""
    if omitted > 0:
        footer = f"\n\n_Omitted {omitted} additional courses beyond the top 15 cap._"

    notes = state.get("research_notes", [])
    metrics = state.get("metrics")
    limitation = ""
    if len(courses) < (state.get("research_plan").min_valid_candidates if state.get("research_plan") else 3):
        limitation = (
            "\n\n_Limitation: fewer validated courses were found than requested. "
            "Uncertain candidates were excluded from the ranked list._"
        )
    if notes:
        limitation += "\n\n_Research notes:_\n" + "\n".join(f"- {note}" for note in notes[-5:])

    digest = "\n\n".join(lines) if lines else "No validated courses found for the selected filters."
    metric_line = ""
    if metrics:
        metric_line = (
            f"\n\n_Metrics: cache hits {metrics.cache_hits}, Tavily calls {metrics.tavily_calls}, "
            f"valid {metrics.valid_count}, rejected {metrics.rejected_count}, uncertain {metrics.uncertain_count}._"
        )
    digest = f"# Personalized Course Digest\n\n{digest}{footer}{limitation}{metric_line}"

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
