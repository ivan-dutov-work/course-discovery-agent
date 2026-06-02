from __future__ import annotations

import time

from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.research_agent.search.tavily_client import TavilyClient


logger = get_logger(__name__)


async def tavily_search_worker_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    query = state.get("active_search_query")
    run_id = state.get("run_id", "unknown")
    if not query:
        return {"research_notes": ["Skipped Tavily worker without active query."]}

    logger.info(
        "tavily_search_start",
        extra={"event": "tavily.search_start", "run_id": run_id, "query": query},
    )
    try:
        results = await TavilyClient().search(query, max_results=5)
        logger.info(
            "tavily_search_complete",
            extra={
                "event": "tavily.search_complete",
                "run_id": run_id,
                "query": query,
                "result_count": len(results),
                "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            },
        )
        return {
            "tavily_results": results,
            "completed_queries": [query],
            "tavily_calls": 1,
        }
    except Exception as exc:  # noqa: BLE001
        err = sanitize_error(exc)
        logger.warning(
            "tavily_search_error",
            extra={
                "event": "tavily.search_error",
                "run_id": run_id,
                "query": query,
                **err,
            },
        )
        return {
            "completed_queries": [query],
            "research_notes": [f"Tavily search failed for '{query}': {err['error_type']}."],
        }
