from __future__ import annotations

from typing import TypedDict

from agent.models import CourseCandidate, RoutingAction, SearchFilters


class AgentState(TypedDict):
    user_query: str
    search_filters: SearchFilters | None
    scraped_courses: list[CourseCandidate]
    deduplicated_courses: list[CourseCandidate]
    digest: str | None
    manager_feedback: str | None
    rewrite_instructions: str | None
    routing_decision: RoutingAction | None
    iteration_count: int
    max_iterations: int
    run_id: str
    worker_a_courses: list[CourseCandidate]
    worker_b_courses: list[CourseCandidate]
    worker_c_courses: list[CourseCandidate]
    error: str | None
    published: bool
    discard_reason: str | None
