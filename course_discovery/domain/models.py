from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class RoutingAction(str, Enum):
    PUBLISH = "PUBLISH"
    REWRITE = "REWRITE"
    AUGMENT = "AUGMENT"
    RESET = "RESET"
    DISCARD = "DISCARD"


class SearchFilters(BaseModel):
    topic: str = Field(default="general programming")
    max_price: float = Field(default=0.0, ge=0)
    min_rating: float = Field(default=0.0, ge=0, le=5)
    include_certificate: bool = Field(default=True)
    certificate_types: list[str] = Field(
        default_factory=lambda: ["free", "paid_optional"]
    )
    content_languages: list[str] = Field(default_factory=lambda: ["en"])
    providers: list[str] = Field(
        default_factory=lambda: ["youtube", "coursera", "edx", "udemy"]
    )
    level: Literal["beginner", "intermediate", "advanced", "any"] = Field(default="any")
    recency_days: int = Field(default=365, ge=1)
    domain_blacklist: list[str] = Field(default_factory=list)


class EvidenceItem(BaseModel):
    source_url: str
    quote_or_summary: str
    supports: list[str] = Field(default_factory=list)


class UserMemory(BaseModel):
    preferred_providers: list[str] = Field(default_factory=list)
    avoided_providers: list[str] = Field(default_factory=list)
    preferred_languages: list[str] = Field(default_factory=list)
    budget_preference: str | None = None
    certificate_importance: Literal["required", "preferred", "irrelevant"] | None = None
    preferred_level: str | None = None
    learning_style_notes: str | None = None
    career_goals: list[str] = Field(default_factory=list)
    completed_course_urls: list[str] = Field(default_factory=list)
    rejected_course_urls: list[str] = Field(default_factory=list)


class ResearchPlan(BaseModel):
    topic: str
    constraints: list[str] = Field(default_factory=list)
    cache_query: str
    search_queries: list[str] = Field(default_factory=list)
    target_sources: list[str] = Field(default_factory=list)
    exclude_patterns: list[str] = Field(default_factory=list)
    min_valid_candidates: int = Field(default=3, ge=1, le=15)
    use_cache_first: bool = True
    freshness_required: bool = False
    rationale: str = ""


class TavilySearchResult(BaseModel):
    query: str
    title: str
    url: str
    snippet: str
    score: float | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class CourseCandidate(BaseModel):
    title: str
    provider: str | None = None
    url: str
    description: str | None = None
    price: str | None = None
    is_free: bool | None = None
    has_certificate: bool | None = None
    level: str | None = None
    language: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    published_or_updated: str | None = None
    source: Literal["cache", "tavily", "manual"]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)


class CandidateValidation(BaseModel):
    url: str
    status: Literal["valid", "rejected", "uncertain"]
    reasons: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)


class ResearchRunMetrics(BaseModel):
    queries_run: int = 0
    cache_hits: int = 0
    tavily_calls: int = 0
    valid_count: int = 0
    rejected_count: int = 0
    uncertain_count: int = 0
    replan_count: int = 0
    unsupported_claim_count: int = 0
    latency_ms: int = 0
    token_count: int = 0


class RoutingDecision(BaseModel):
    action: RoutingAction
    rewrite_instructions: str = ""
