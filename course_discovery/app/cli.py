from __future__ import annotations

import asyncio
import time
import importlib
from importlib import util as importlib_util
from uuid import uuid4

from langchain_core.runnables import RunnableConfig

from course_discovery.domain.models import DeliveryStatus, ResearchRunMetrics
from course_discovery.domain.state import AgentState
from course_discovery.observability.logging import (
    classify_feedback,
    configure_logging,
    get_logger,
    preview,
)
from course_discovery.observability.metrics import (
    configure_metrics,
    record_review_wait,
    record_run_outcome,
    shutdown_metrics,
)
from course_discovery.observability.tracing import (
    configure_tracing,
    annotate_run,
    run_span,
    shutdown_tracing,
)
from course_discovery.guardrails import redact_pii
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.privacy import authorize_thread, register_thread
from course_discovery.resilience import RECURSION_LIMIT
from course_discovery.workflows.outer_graph import build_graph


def _initial_state(query: str) -> AgentState:
    return {
        "user_query": redact_pii(query) or "",
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
        "feedback_history": [],
        "rewrite_instructions": None,
        "routing_decision": None,
        "research_iteration": 0,
        "completed_queries": [],
        "research_notes": [],
        "metrics": ResearchRunMetrics(),
        "active_search_query": None,
        "error": None,
        "publish_status": None,
        "discard_reason": None,
        "memory_update": None,
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
    configure_tracing("course-agent-cli")
    configure_metrics("course-agent-cli")

    try:
        async with open_checkpointer() as saver:
            try:
                await _run(build_graph(checkpointer=saver))
            except Exception:
                record_run_outcome("error")
                raise
    finally:
        shutdown_tracing()
        shutdown_metrics()


def _print_progress(namespace: tuple[str, ...], mode: str, chunk) -> None:
    prefix = "  " * len(namespace)
    if mode == "updates":
        for node in chunk:
            if not node.startswith("__"):
                print(f"{prefix}- {node}")
    elif mode == "custom":
        print(f"{prefix}  {chunk}")


async def _stream_until_pause(graph, graph_input, config: RunnableConfig, *, resume: bool) -> dict:
    run_id = config["configurable"]["thread_id"]
    with run_span(
        "run.resume" if resume else "run.start",
        run_id=run_id,
        thread_id=run_id,
        resume=resume,
    ) as span:
        async for namespace, mode, chunk in graph.astream(
            graph_input,
            config,
            stream_mode=["updates", "custom"],
            subgraphs=True,
        ):
            _print_progress(namespace, mode, chunk)
        snapshot = await graph.aget_state(config)
        annotate_run(span, snapshot.values)
    return snapshot.values


async def _run(graph) -> None:
    logger = get_logger(__name__)

    query = (
        input("Enter query (leave blank for default): ").strip()
        or "Find free Python courses with certificate for beginners"
    )

    run_id = str(uuid4())
    config: RunnableConfig = {
        "configurable": {"thread_id": run_id},
        "recursion_limit": RECURSION_LIMIT,
    }
    start_ts = time.perf_counter()
    review_wait = 0.0

    def active_ms() -> int:
        return int((time.perf_counter() - start_ts - review_wait) * 1000)

    logger.info(
        "run_start",
        extra={
            "event": "main.run_start",
            "run_id": run_id,
            "thread_id": run_id,
            "query_preview": preview(query, max_len=100),
            "query_len": len(query),
        },
    )

    print(f"\nRun ID: {run_id}")
    print("\nStarting graph execution...\n")

    initial_state = _initial_state(query)
    register_thread(initial_state["user_id"], run_id)
    result = await _stream_until_pause(graph, initial_state, config, resume=False)

    for _ in range(10):
        if not (await graph.aget_state(config)).next:
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
            f"Cache hits: {result['metrics'].cache_hits} | "
            f"Tavily calls: {result['metrics'].tavily_calls} | "
            f"Valid: {len(result.get('valid_courses', []))} | "
            f"Uncertain: {len(result.get('uncertain_courses', []))}"
        )

        wait_start = time.perf_counter()
        pm_feedback = input(
            "PM feedback (approve | rewrite: ... | augment: ... | reset: ... | discard): "
        ).strip()
        waited = time.perf_counter() - wait_start
        review_wait += waited
        record_review_wait(waited)

        logger.info(
            "feedback_received",
            extra={
                "event": "main.feedback_received",
                "run_id": run_id,
                "feedback_type": classify_feedback(pm_feedback),
                "feedback_preview": preview(pm_feedback, max_len=80),
                "feedback_len": len(pm_feedback),
            },
        )

        authorize_thread(initial_state["user_id"], run_id)
        await graph.aupdate_state(
            config, {"manager_feedback": redact_pii(pm_feedback)}
        )
        logger.info(
            "invoke_resume",
            extra={
                "event": "main.invoke_resume",
                "run_id": run_id,
                "thread_id": run_id,
            },
        )
        register_thread(initial_state["user_id"], run_id)
        result = await _stream_until_pause(graph, None, config, resume=True)

        publish_status = result.get("publish_status")
        if publish_status:
            print(f"Digest {DeliveryStatus(publish_status).value}.")
            record_run_outcome("publish", delivery=DeliveryStatus(publish_status).value)
            logger.info(
                "run_complete",
                extra={
                    "event": "main.run_complete",
                    "run_id": run_id,
                    "final_action": "PUBLISH",
                    "duration_ms": active_ms(),
                    "review_wait_ms": int(review_wait * 1000),
                    "iterations": len(result.get("feedback_history") or []),
                },
            )
            break
    else:
        print("Stopped after loop safety limit.")
        record_run_outcome("loop_stop")
        logger.warning(
            "run_loop_safety_stop",
            extra={
                "event": "main.run_loop_safety_stop",
                "run_id": run_id,
                "duration_ms": active_ms(),
                "review_wait_ms": int(review_wait * 1000),
                "iterations": len(result.get("feedback_history") or []),
            },
        )

    if not result.get("publish_status") and not (await graph.aget_state(config)).next:
        record_run_outcome("discard")
        logger.info(
            "run_complete",
            extra={
                "event": "main.run_complete",
                "run_id": run_id,
                "final_action": "DISCARD",
                "discard_reason": preview(
                    result.get("discard_reason"), max_len=140
                ),
                "duration_ms": active_ms(),
                "review_wait_ms": int(review_wait * 1000),
                "iterations": len(result.get("feedback_history") or []),
            },
        )


if __name__ == "__main__":
    asyncio.run(main())
