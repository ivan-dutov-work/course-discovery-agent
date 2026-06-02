from __future__ import annotations

from urllib.parse import urlparse

from course_discovery.domain.models import CourseCandidate, EvidenceItem
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger


logger = get_logger(__name__)


def _provider_from_url(url: str) -> str | None:
    host = urlparse(url).hostname or ""
    parts = host.removeprefix("www.").split(".")
    return parts[0] if parts else None


def _is_aggregator(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(
        marker in host
        for marker in ["medium.com", "reddit.com", "quora.com", "classcentral.com"]
    )


def _extract_result(result) -> CourseCandidate | None:
    payload = f"{result.title} {result.snippet}".lower()
    if _is_aggregator(result.url) and "course" not in payload:
        return None

    supports = ["course"]
    if "free" in payload or "$0" in payload:
        supports.append("free")
    if "certificate" in payload or "certification" in payload:
        supports.append("certificate")
    for level in ["beginner", "intermediate", "advanced"]:
        if level in payload:
            supports.append(level)

    return CourseCandidate(
        title=result.title,
        provider=_provider_from_url(result.url),
        url=result.url,
        description=result.snippet or None,
        price="free" if "free" in supports else None,
        is_free=True if "free" in supports else None,
        has_certificate=True if "certificate" in supports else None,
        level=next((level for level in ["beginner", "intermediate", "advanced"] if level in supports), None),
        language="en",
        rating=None,
        published_or_updated=None,
        source="tavily",
        evidence=[
            EvidenceItem(
                source_url=result.url,
                quote_or_summary=result.snippet or result.title,
                supports=supports,
            )
        ],
        confidence=0.55 + (0.2 if result.score and result.score > 0.7 else 0.0),
    )


def candidate_extractor_node(state: AgentState) -> dict:
    candidates = []
    for result in state.get("tavily_results", []):
        candidate = _extract_result(result)
        if candidate is not None:
            candidates.append(candidate)

    logger.info(
        "candidate_extraction_complete",
        extra={
            "event": "extractor.complete",
            "run_id": state.get("run_id", "unknown"),
            "result_count": len(state.get("tavily_results", [])),
            "candidate_count": len(candidates),
        },
    )
    return {"extracted_candidates": candidates}
