from __future__ import annotations

import operator
from typing import Annotated
from typing import TypedDict

from course_discovery.domain.models import (
    CandidateValidation,
    CourseCandidate,
    ResearchPlan,
    ResearchRunMetrics,
    RoutingAction,
    SearchFilters,
    TavilySearchResult,
    UserMemory,
)


class AgentState(TypedDict):
    user_query: str
    user_id: str | None
    search_filters: SearchFilters | None
    user_memory: UserMemory | None
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
    manager_feedback: str | None
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
    published: bool
    discard_reason: str | None
