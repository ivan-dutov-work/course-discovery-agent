from __future__ import annotations

import re

FACET_WORDS = (
    "free",
    "beginner",
    "intermediate",
    "advanced",
    "certificate",
    "certification",
    "certified",
)

_FACETS = re.compile(r"\b(?:" + "|".join(FACET_WORDS) + r")s?\b", re.IGNORECASE)


def strip_facets(text: str) -> str:
    return re.sub(r"\s+", " ", _FACETS.sub(" ", text)).strip()
