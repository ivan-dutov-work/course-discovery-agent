from __future__ import annotations

import re

from course_discovery.domain.models import SearchFilters, UserMemory

_PRICE_WORDS = re.compile(r"\b(free|paid|cheap|budget|price|cost|usd|eur)\b|[$€£]\s?\d|\bunder\s+\d+", re.I)
_CERTIFICATE_WORDS = re.compile(r"certificat|certified|accredit", re.I)

_BUDGET_KEYWORDS = {"free": 0.0, "paid": 999.0, "any": 999.0}


def _max_price(budget: str | None) -> float | None:
    if budget is None:
        return None
    text = budget.strip().lower()
    if text in _BUDGET_KEYWORDS:
        return _BUDGET_KEYWORDS[text]
    try:
        return max(float(text), 0.0)
    except ValueError:
        return None


def apply_profile_defaults(filters: SearchFilters, query: str, memory: UserMemory) -> SearchFilters:
    updates: dict[str, object] = {}
    if not _PRICE_WORDS.search(query):
        max_price = _max_price(memory.budget_preference)
        if max_price is not None:
            updates["max_price"] = max_price
    if not _CERTIFICATE_WORDS.search(query) and memory.certificate_importance is not None:
        updates["include_certificate"] = memory.certificate_importance != "irrelevant"
    return filters.model_copy(update=updates) if updates else filters
