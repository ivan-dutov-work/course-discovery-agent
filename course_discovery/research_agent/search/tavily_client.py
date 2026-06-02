from __future__ import annotations

import asyncio
import json
import os
from urllib import request

from course_discovery.domain.models import TavilySearchResult


class TavilyClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or os.getenv("TAVILY_API_KEY")

    async def search(
        self,
        query: str,
        *,
        max_results: int = 5,
    ) -> list[TavilySearchResult]:
        if not self.api_key:
            raise RuntimeError("TAVILY_API_KEY is required for Tavily search")
        return await asyncio.to_thread(self._search_sync, query, max_results)

    def _search_sync(self, query: str, max_results: int) -> list[TavilySearchResult]:
        payload = json.dumps(
            {
                "api_key": self.api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "basic",
                "include_answer": False,
                "include_raw_content": False,
            }
        ).encode("utf-8")
        req = request.Request(
            "https://api.tavily.com/search",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=15) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8"))

        results = []
        for item in data.get("results", []):
            results.append(
                TavilySearchResult(
                    query=query,
                    title=item.get("title") or "Untitled result",
                    url=item.get("url") or "",
                    snippet=item.get("content") or item.get("snippet") or "",
                    score=item.get("score"),
                    raw_metadata={
                        key: value
                        for key, value in item.items()
                        if key not in {"title", "url", "content", "snippet", "score"}
                    },
                )
            )
        return [result for result in results if result.url]
