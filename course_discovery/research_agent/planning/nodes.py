from __future__ import annotations

from course_discovery.domain.models import (
    ResearchPlan,
    ResearchRunMetrics,
    SearchFilters,
)
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger


logger = get_logger(__name__)


def _constraints(filters: SearchFilters) -> list[str]:
    constraints = [f"topic:{filters.topic}", f"level:{filters.level}"]
    if filters.max_price == 0:
        constraints.append("free")
    if filters.include_certificate:
        constraints.append("certificate")
    if filters.min_rating:
        constraints.append(f"rating>={filters.min_rating}")
    if filters.content_languages:
        constraints.append(f"languages:{','.join(filters.content_languages)}")
    return constraints


def _queries(filters: SearchFilters, *, cache_count: int, iteration: int) -> list[str]:
    topic = filters.topic.strip() or "online course"
    terms = [topic, "course"]
    if filters.level != "any":
        terms.append(filters.level)
    if filters.max_price == 0:
        terms.append("free")
    if filters.include_certificate:
        terms.append("certificate")

    base = " ".join(terms)
    queries = [base]
    if cache_count == 0 or filters.include_certificate:
        queries.append(f"{base} official course page")
    if iteration > 0:
        queries.append(f"{base} updated recent")
    return list(dict.fromkeys(queries))[:4]


def research_planner_node(state: AgentState) -> dict:
    filters = state.get("search_filters")
    if filters is None:
        return {
            "research_plan": None,
            "error": "Research planning failed: search filters missing",
        }

    cache_candidates = state.get("cache_candidates", [])
    min_valid = 3
    needs_web = len(cache_candidates) < min_valid
    search_queries = (
        _queries(
            filters,
            cache_count=len(cache_candidates),
            iteration=state.get("research_iteration", 0),
        )
        if needs_web
        else []
    )
    completed = set(state.get("completed_queries", []))
    search_queries = [query for query in search_queries if query not in completed]

    plan = ResearchPlan(
        topic=filters.topic,
        constraints=_constraints(filters),
        cache_query=filters.topic,
        search_queries=search_queries,
        target_sources=filters.providers,
        exclude_patterns=filters.domain_blacklist,
        min_valid_candidates=min_valid,
        use_cache_first=True,
        freshness_required=filters.recency_days <= 180,
        rationale=(
            "Cache has enough candidates; validate before synthesis."
            if not needs_web
            else "Cache is insufficient or missing evidence; search only planned gaps."
        ),
    )
    logger.info(
        "research_plan_complete",
        extra={
            "event": "planner.complete",
            "run_id": state.get("run_id", "unknown"),
            "cache_candidate_count": len(cache_candidates),
            "search_query_count": len(search_queries),
            "min_valid_candidates": min_valid,
        },
    )
    return {"research_plan": plan}


def need_web_search(state: AgentState):
    plan = state.get("research_plan")
    if state.get("error"):
        return "discard_node"
    if plan and plan.search_queries:
        return "tavily_search_workers"
    return "aggregate"


def replanner_node(state: AgentState) -> dict:
    iteration = state.get("research_iteration", 0) + 1
    filters = state.get("search_filters")
    if filters is None:
        return {"error": "Replanning failed: search filters missing"}

    missing = sorted(
        {
            item
            for validation in state.get("validation_results", [])
            for item in validation.missing_evidence
        }
    )
    current_plan = state.get("research_plan")
    completed = set(state.get("completed_queries", []))
    base_queries = _queries(
        filters,
        cache_count=len(state.get("valid_courses", [])),
        iteration=iteration,
    )
    if missing:
        base_queries.append(f"{filters.topic} {' '.join(missing)} course evidence")

    search_queries = [query for query in dict.fromkeys(base_queries) if query not in completed]
    plan = ResearchPlan(
        topic=filters.topic,
        constraints=_constraints(filters),
        cache_query=current_plan.cache_query if current_plan else filters.topic,
        search_queries=search_queries[:4],
        target_sources=filters.providers,
        exclude_patterns=filters.domain_blacklist,
        min_valid_candidates=current_plan.min_valid_candidates if current_plan else 3,
        use_cache_first=False,
        freshness_required=True,
        rationale="Replanned from validation failures and missing evidence.",
    )
    metrics = (state.get("metrics") or ResearchRunMetrics()).model_copy(
        update={"replan_count": iteration}
    )
    logger.info(
        "research_replan_complete",
        extra={
            "event": "planner.replan_complete",
            "run_id": state.get("run_id", "unknown"),
            "research_iteration": iteration,
            "search_query_count": len(plan.search_queries),
            "missing_evidence": missing,
        },
    )
    return {
        "research_plan": plan,
        "research_iteration": iteration,
        "metrics": metrics,
        "tavily_results": [],
        "extracted_candidates": [],
    }
