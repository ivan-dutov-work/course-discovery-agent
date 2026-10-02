from __future__ import annotations

import asyncio
import time

from langgraph.config import get_stream_writer

from course_discovery.domain.run_config import current_run_id
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.observability.metrics import record_degradation, record_search
from course_discovery.research_agent.search.tavily_client import TavilyClient
from course_discovery.resilience import SEARCH_TIMEOUT_SECONDS, is_transient


logger = get_logger(__name__)


def _emit_progress(message: str) -> None:
    try:
        get_stream_writer()(message)
    except RuntimeError:
        pass


async def tavily_search_worker_node(state: AgentState) -> dict:
    start_ts = time.perf_counter()
    query = state.get("active_search_query")
    run_id = current_run_id()
    if not query:
        return {"research_notes": ["Skipped Tavily worker without active query."]}

    logger.info(
        "tavily_search_start",
        extra={"event": "tavily.search_start", "run_id": run_id, "query_len": len(query)},
    )
    try:
        results = await asyncio.wait_for(
            TavilyClient().search(query, max_results=5), SEARCH_TIMEOUT_SECONDS
        )
        logger.info(
            "tavily_search_complete",
            extra={
                "event": "tavily.search_complete",
                "run_id": run_id,
                "query_len": len(query),
                "result_count": len(results),
                "duration_ms": int((time.perf_counter() - start_ts) * 1000),
            },
        )
        record_search("ok" if results else "empty", time.perf_counter() - start_ts)
        _emit_progress(f"searched '{query}': {len(results)} results")
        return {
            "tavily_results": results,
            "completed_queries": [query],
        }
    except Exception as exc:  # noqa: BLE001
        if is_transient(exc):
            record_search("transient", time.perf_counter() - start_ts)
            raise
        record_search("error", time.perf_counter() - start_ts)
        record_degradation("search", "query_failed")
        err = sanitize_error(exc)
        logger.warning(
            "tavily_search_error",
            extra={
                "event": "tavily.search_error",
                "run_id": run_id,
                "query_len": len(query),
                **err,
            },
        )
        return {
            "completed_queries": [query],
            "research_notes": [f"Tavily search failed for '{query}': {err['error_type']}."],
        }
