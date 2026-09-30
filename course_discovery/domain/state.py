from __future__ import annotations

import operator
from typing import Annotated, TypedDict, get_type_hints

from course_discovery.domain.pii import Pii
from course_discovery.domain.models import (
    CandidateValidation,
    CourseCandidate,
    DeliveryStatus,
    ResearchPlan,
    ResearchRunMetrics,
    RoutingAction,
    SearchFilters,
    TavilySearchResult,
    UserMemory,
)


class AgentState(TypedDict):
    user_query: Annotated[str, Pii(subject="user_id")]
    user_id: str | None
    search_filters: SearchFilters | None
    user_memory: Annotated[UserMemory | None, Pii(subject="user_id", redacted=False)]
    cache_candidates: list[CourseCandidate]
    research_plan: ResearchPlan | None
    tavily_results: Annotated[list[TavilySearchResult], operator.add]
    extracted_candidates: list[CourseCandidate]
    scraped_courses: list[CourseCandidate]
    deduplicated_courses: list[CourseCandidate]
    valid_courses: list[CourseCandidate]
    rejected_courses: list[CourseCandidate]
    uncertain_courses: list[CourseCandidate]
    validation_results: list[CandidateValidation]
    digest: str | None
    manager_feedback: Annotated[str | None, Pii(subject="user_id")]
    feedback_history: Annotated[list[str], Pii(subject="user_id"), operator.add]
    rewrite_instructions: str | None
    routing_decision: RoutingAction | None
    iteration_count: int
    max_iterations: int
    research_iteration: int
    max_research_iterations: int
    completed_queries: Annotated[list[str], operator.add]
    research_notes: Annotated[list[str], operator.add]
    cache_hits: int
    tavily_calls: Annotated[int, operator.add]
    metrics: ResearchRunMetrics
    run_id: str
    active_search_query: str | None
    error: str | None
    publish_status: DeliveryStatus | None
    discard_reason: str | None
    memory_update: str | None


OUTER_ONLY_CHANNELS = frozenset({"feedback_history"})

ResearchState = TypedDict(
    "ResearchState",
    {
        name: hint
        for name, hint in get_type_hints(AgentState, include_extras=True).items()
        if name not in OUTER_ONLY_CHANNELS
    },
)
