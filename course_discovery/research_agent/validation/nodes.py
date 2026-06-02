from __future__ import annotations

from course_discovery.domain.models import (
    CandidateValidation,
    CourseCandidate,
    ResearchRunMetrics,
)
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger


logger = get_logger(__name__)


def aggregate_node(state: AgentState) -> dict:
    merged = state.get("cache_candidates", []) + state.get("extracted_candidates", [])
    logger.info(
        "aggregate_complete",
        extra={
            "event": "aggregate.complete",
            "run_id": state.get("run_id", "unknown"),
            "cache_count": len(state.get("cache_candidates", [])),
            "web_count": len(state.get("extracted_candidates", [])),
            "merged_count": len(merged),
        },
    )
    return {"scraped_courses": merged[:200]}


def _validate_candidate(state: AgentState, candidate: CourseCandidate) -> CandidateValidation:
    filters = state.get("search_filters")
    memory = state.get("user_memory")
    reasons: list[str] = []
    missing: list[str] = []

    if memory and candidate.url in memory.completed_course_urls:
        reasons.append("already completed by user")
    if memory and candidate.url in memory.rejected_course_urls:
        reasons.append("previously rejected by user")
    if memory and candidate.provider in memory.avoided_providers:
        reasons.append("provider is avoided by user")

    if filters:
        if filters.max_price == 0 and candidate.is_free is not True:
            missing.append("free")
        if filters.include_certificate and candidate.has_certificate is not True:
            missing.append("certificate")
        if filters.level != "any" and candidate.level not in {filters.level, None}:
            reasons.append(f"level mismatch: expected {filters.level}, got {candidate.level}")
        if candidate.language and candidate.language not in filters.content_languages:
            reasons.append(f"language mismatch: {candidate.language}")
        if filters.min_rating and candidate.rating is not None and candidate.rating < filters.min_rating:
            reasons.append(f"rating below {filters.min_rating}")

    supported = {item for evidence in candidate.evidence for item in evidence.supports}
    missing = [item for item in missing if item not in supported]

    if reasons:
        status = "rejected"
    elif missing:
        status = "uncertain"
    else:
        status = "valid"

    if status == "valid":
        reasons.append("critical constraints have supporting evidence")

    return CandidateValidation(
        url=candidate.url,
        status=status,
        reasons=reasons,
        missing_evidence=missing,
    )


def evidence_validator_node(state: AgentState) -> dict:
    validations = [_validate_candidate(state, item) for item in state.get("deduplicated_courses", [])]
    by_url = {item.url: item for item in validations}
    valid = [
        item
        for item in state.get("deduplicated_courses", [])
        if by_url[item.url].status == "valid"
    ]
    rejected = [
        item
        for item in state.get("deduplicated_courses", [])
        if by_url[item.url].status == "rejected"
    ]
    uncertain = [
        item
        for item in state.get("deduplicated_courses", [])
        if by_url[item.url].status == "uncertain"
    ]
    metrics = (state.get("metrics") or ResearchRunMetrics()).model_copy(
        update={
            "valid_count": len(valid),
            "rejected_count": len(rejected),
            "uncertain_count": len(uncertain),
            "queries_run": len(state.get("completed_queries", [])),
            "tavily_calls": state.get("tavily_calls", 0),
            "unsupported_claim_count": sum(len(item.missing_evidence) for item in validations),
        }
    )
    logger.info(
        "evidence_validation_complete",
        extra={
            "event": "validator.complete",
            "run_id": state.get("run_id", "unknown"),
            "valid_count": len(valid),
            "rejected_count": len(rejected),
            "uncertain_count": len(uncertain),
        },
    )
    return {
        "validation_results": validations,
        "valid_courses": valid,
        "rejected_courses": rejected,
        "uncertain_courses": uncertain,
        "metrics": metrics,
    }


def enough_valid(state: AgentState):
    plan = state.get("research_plan")
    min_valid = plan.min_valid_candidates if plan else 3
    if len(state.get("valid_courses", [])) >= min_valid:
        return "course_cache_upsert"
    if state.get("research_iteration", 0) < state.get("max_research_iterations", 2):
        return "replanner"
    return "course_cache_upsert"
