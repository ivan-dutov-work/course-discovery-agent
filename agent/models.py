from __future__ import annotations

from enum import Enum
from typing import Literal

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


class CourseCandidate(BaseModel):
    title: str
    url: str
    provider: str
    description: str
    price: float = Field(ge=0)
    rating: float = Field(ge=0, le=5)
    certificate_type: str
    source_worker: str
    language: str
    last_updated: str
    enrollment_count: int = Field(ge=0)


class RoutingDecision(BaseModel):
    action: RoutingAction
    rewrite_instructions: str = ""
