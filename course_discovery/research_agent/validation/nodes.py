from __future__ import annotations

from typing import Literal

from langgraph.types import Command

from course_discovery.domain.models import (
    CandidateValidation,
    CourseCandidate,
    ResearchRunMetrics,
)
from course_discovery.domain.run_config import current_run_id, max_research_iterations
from course_discovery.domain.state import ResearchState
from course_discovery.observability.logging import get_logger
from course_discovery.observability.metrics import record_degradation, record_validation


logger = get_logger(__name__)


def aggregate_node(state: ResearchState) -> dict:
    merged = state.get("cache_candidates", []) + state.get("extracted_candidates", [])
    logger.info(
        "aggregate_complete",
        extra={
            "event": "aggregate.complete",
            "run_id": current_run_id(),
            "cache_count": len(state.get("cache_candidates", [])),
            "web_count": len(state.get("extracted_candidates", [])),
            "merged_count": len(merged),
        },
    )
    return {"scraped_courses": merged[:200]}


def _validate_candidate(state: ResearchState, candidate: CourseCandidate) -> CandidateValidation:
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


def evidence_validator_node(
    state: ResearchState,
) -> Command[Literal["save_verified_courses", "plan_gap_search"]]:
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
            "tavily_calls": len(state.get("completed_queries", [])),
            "unsupported_claim_count": sum(len(item.missing_evidence) for item in validations),
        }
    )
    record_validation(valid=len(valid), rejected=len(rejected), uncertain=len(uncertain))
    logger.info(
        "evidence_validation_complete",
        extra={
            "event": "validator.complete",
            "run_id": current_run_id(),
            "valid_count": len(valid),
            "rejected_count": len(rejected),
            "uncertain_count": len(uncertain),
        },
    )
    update = {
        "validation_results": validations,
        "valid_courses": valid,
        "rejected_courses": rejected,
        "uncertain_courses": uncertain,
        "metrics": metrics,
    }
    return Command(update=update, goto=enough_valid({**state, **update}))


def enough_valid(state: ResearchState):
    plan = state.get("research_plan")
    min_valid = plan.min_valid_candidates if plan else 3
    if len(state.get("valid_courses", [])) >= min_valid:
        return "save_verified_courses"
    if state.get("research_iteration", 0) < max_research_iterations():
        return "plan_gap_search"
    record_degradation("research", "replan_budget_exhausted")
    logger.warning(
        "replan_budget_exhausted",
        extra={
            "event": "validator.replan_budget_exhausted",
            "run_id": current_run_id(),
            "valid_count": len(state.get("valid_courses", [])),
            "min_valid": min_valid,
            "research_iteration": state.get("research_iteration", 0),
        },
    )
    return "save_verified_courses"
