from __future__ import annotations

import operator
from typing import Annotated, TypedDict

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
    valid_courses: list[CourseCandidate]
    digest: str | None
    metrics: ResearchRunMetrics
    manager_feedback: Annotated[str | None, Pii(subject="user_id")]
    feedback_history: Annotated[list[str], Pii(subject="user_id"), operator.add]
    rewrite_instructions: str | None
    routing_decision: RoutingAction | None
    publish_status: DeliveryStatus | None
    discard_reason: str | None
    memory_update: str | None


class ResearchInput(TypedDict):
    user_id: str | None
    search_filters: SearchFilters | None
    routing_decision: RoutingAction | None
    rewrite_instructions: str | None


class ResearchOutput(TypedDict):
    valid_courses: list[CourseCandidate]
    digest: str | None
    metrics: ResearchRunMetrics


class ResearchState(ResearchInput, ResearchOutput):
    user_memory: Annotated[UserMemory | None, Pii(subject="user_id", redacted=False)]
    cache_candidates: list[CourseCandidate]
    research_plan: ResearchPlan | None
    tavily_results: Annotated[list[TavilySearchResult], operator.add]
    extracted_candidates: list[CourseCandidate]
    scraped_courses: list[CourseCandidate]
    deduplicated_courses: list[CourseCandidate]
    rejected_courses: list[CourseCandidate]
    uncertain_courses: list[CourseCandidate]
    validation_results: list[CandidateValidation]
    research_iteration: int
    completed_queries: Annotated[list[str], operator.add]
    research_notes: Annotated[list[str], operator.add]
    active_search_query: str | None
    error: str | None
