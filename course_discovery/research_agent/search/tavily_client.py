from __future__ import annotations

import asyncio
import re

from course_discovery.domain.models import TavilySearchResult
from course_discovery.research_agent.search.mock_catalog import CATALOG, MockListing


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


class TavilyClient:
    """Deterministic stand-in for the Tavily search API.

    The article is about LangGraph's control-flow patterns (fan-out, reducers,
    replanning), not about web search. A production build would swap this class
    for a real Tavily/Serper/Brave client behind the same `search()` signature
    without touching any graph or node code.
    """

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[TavilySearchResult]:
        return await asyncio.to_thread(self._search_sync, query, max_results)

    def _search_sync(self, query: str, max_results: int) -> list[TavilySearchResult]:
        query_tokens = _tokenize(query)
        scored: list[tuple[float, MockListing]] = []
        for listing in CATALOG:
            listing_tokens = _tokenize(" ".join(listing.keywords) + " " + listing.title)
            overlap = len(query_tokens & listing_tokens)
            if overlap == 0:
                continue
            score = overlap / max(len(query_tokens), 1)
            scored.append((score, listing))

        scored.sort(key=lambda pair: pair[0], reverse=True)
        results = [
            TavilySearchResult(
                query=query,
                title=listing.title,
                url=listing.url,
                snippet=listing.snippet,
                score=round(score, 3),
                raw_metadata={"mock": True},
            )
            for score, listing in scored[:max_results]
        ]
        return results
