from __future__ import annotations

import asyncio
import time
import importlib
from importlib import util as importlib_util
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from course_discovery.domain.models import ResearchRunMetrics, RoutingAction
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import (
    classify_feedback,
    configure_logging,
    get_logger,
    truncate_text,
)
from course_discovery.workflows.outer_graph import build_graph


def _initial_state(query: str, run_id: str) -> AgentState:
    return {
        "user_query": query,
        "user_id": "cli-user",
        "search_filters": None,
        "user_memory": None,
        "cache_candidates": [],
        "research_plan": None,
        "tavily_results": [],
        "extracted_candidates": [],
        "scraped_courses": [],
        "deduplicated_courses": [],
        "valid_courses": [],
        "rejected_courses": [],
        "uncertain_courses": [],
        "validation_results": [],
        "digest": None,
        "manager_feedback": None,
        "rewrite_instructions": None,
        "routing_decision": None,
        "iteration_count": 0,
        "max_iterations": 3,
        "research_iteration": 0,
        "max_research_iterations": 2,
        "completed_queries": [],
        "research_notes": [],
        "cache_hits": 0,
        "tavily_calls": 0,
        "metrics": ResearchRunMetrics(),
        "run_id": run_id,
        "active_search_query": None,
        "error": None,
        "published": False,
        "discard_reason": None,
    }


async def main() -> None:
    dotenv = (
        importlib.import_module("dotenv")
        if importlib_util.find_spec("dotenv")
        else None
    )
    if dotenv is not None:
        dotenv.load_dotenv()
    configure_logging()
    logger = get_logger(__name__)

    graph = build_graph()

    query = (
        input("Enter query (leave blank for default): ").strip()
        or "Find free Python courses with certificate for beginners"
    )

    run_id = str(uuid4())
    config: RunnableConfig = {"configurable": {"thread_id": run_id}}
    start_ts = time.perf_counter()

    logger.info(
        "run_start",
        extra={
            "event": "main.run_start",
            "run_id": run_id,
            "thread_id": run_id,
            "query_preview": truncate_text(query, max_len=100),
            "query_len": len(query),
        },
    )

    print(f"\nRun ID: {run_id}")
    print("\nStarting graph execution...\n")

    result = await graph.ainvoke(_initial_state(query, run_id), config)

    for _ in range(10):
        routing_decision = result.get("routing_decision")
        if routing_decision in {
            "PUBLISH",
            "DISCARD",
            RoutingAction.PUBLISH,
            RoutingAction.DISCARD,
        }:
            break

        digest = result.get("digest") or "<No digest generated>"
        logger.info(
            "interrupt_reached",
            extra={
                "event": "main.interrupt_reached",
                "run_id": run_id,
                "digest_len": len(digest),
            },
        )
        print("\n=== DIGEST READY FOR REVIEW ===")
        print(digest)
        print("=== END DIGEST ===\n")
        print(
            f"Cache hits: {result.get('cache_hits', 0)} | "
            f"Tavily calls: {result.get('tavily_calls', 0)} | "
            f"Valid: {len(result.get('valid_courses', []))} | "
            f"Uncertain: {len(result.get('uncertain_courses', []))}"
        )

        pm_feedback = input(
            "PM feedback (approve | rewrite: ... | augment: ... | reset: ... | discard): "
        ).strip()

        logger.info(
            "feedback_received",
            extra={
                "event": "main.feedback_received",
                "run_id": run_id,
                "feedback_type": classify_feedback(pm_feedback),
                "feedback_preview": truncate_text(pm_feedback, max_len=80),
                "feedback_len": len(pm_feedback),
            },
        )

        graph.update_state(config, {"manager_feedback": pm_feedback})
        logger.info(
            "invoke_resume",
            extra={
                "event": "main.invoke_resume",
                "run_id": run_id,
                "thread_id": run_id,
            },
        )
        result = await graph.ainvoke(None, config)

        if result.get("published"):
            print("Digest published.")
            logger.info(
                "run_complete",
                extra={
                    "event": "main.run_complete",
                    "run_id": run_id,
                    "final_action": "PUBLISH",
                    "duration_ms": int((time.perf_counter() - start_ts) * 1000),
                    "iterations": result.get("iteration_count", 0),
                },
            )
            break
    else:
        print("Stopped after loop safety limit.")
        logger.warning(
            "run_loop_safety_stop",
            extra={
                "event": "main.run_loop_safety_stop",
                "run_id": run_id,
                "duration_ms": int((time.perf_counter() - start_ts) * 1000),
                "iterations": result.get("iteration_count", 0),
            },
        )

    if not result.get("published") and result.get("routing_decision") in {
        "DISCARD",
        RoutingAction.DISCARD,
    }:
        logger.info(
            "run_complete",
            extra={
                "event": "main.run_complete",
                "run_id": run_id,
                "final_action": "DISCARD",
                "discard_reason": truncate_text(
                    result.get("discard_reason"), max_len=140
                ),
                "duration_ms": int((time.perf_counter() - start_ts) * 1000),
                "iterations": result.get("iteration_count", 0),
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
