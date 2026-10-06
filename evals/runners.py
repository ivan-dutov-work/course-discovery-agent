from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

import psycopg
from langchain_core.runnables import RunnableConfig

from course_discovery.app.cli import _initial_state
from course_discovery.domain.models import (
    CourseCandidate,
    EvidenceItem,
    ResearchPlan,
    SearchFilters,
    UserMemory,
)
from course_discovery.effects.factory import set_gateway
from course_discovery.effects.gateway import InlineGateway
from course_discovery.effects.memory_store import InMemoryOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.persistence.checkpointer import memory_saver, open_checkpointer
from course_discovery.privacy import IncompatibleThreadError, ThreadAccessError, authorize_thread, register_thread
from course_discovery.research_agent.cache.dedup import dedup_node
from course_discovery.research_agent.planning.nodes import research_planner_node
from course_discovery.research_agent.validation.nodes import enough_valid, evidence_validator_node
from course_discovery.workflows.outer_graph import build_graph
from evals.cases import Case

BASE_FILTERS = {"topic": "python", "max_price": 0, "include_certificate": True, "level": "beginner"}
BASE_CANDIDATE = {
    "title": "Python Basics",
    "provider": "example",
    "url": "https://example.com/python",
    "source": "manual",
    "is_free": True,
    "has_certificate": True,
    "level": "beginner",
    "language": "en",
    "confidence": 0.5,
}
PUBLISH_ROUTES = {
    "PUBLISH": "send_approved_courses",
    "REWRITE": "start_research_pass",
    "AUGMENT": "start_research_pass",
    "RESET": "parse_user_request",
    "DISCARD": "discard_run",
}
LIVE_ENV = ("DATABASE_URL", "OPENROUTER_API_KEY")


def _candidate(overrides: dict) -> CourseCandidate:
    raw = {**BASE_CANDIDATE, **overrides}
    raw["evidence"] = [EvidenceItem(**item) for item in raw.get("evidence", [])]
    return CourseCandidate(**raw)


def _state(case: Case, **extra: Any) -> dict:
    return {
        "search_filters": SearchFilters(**{**BASE_FILTERS, **case.input.get("filters", {})}),
        "user_memory": UserMemory(**case.input.get("memory", {})),
        **extra,
    }


def _run_validator(case: Case) -> dict:
    candidate = _candidate(case.input.get("candidate", {}))
    update = evidence_validator_node(_state(case, deduplicated_courses=[candidate]))
    verdict = update["validation_results"][0]
    return {
        "status": verdict.status,
        "missing_evidence": verdict.missing_evidence,
        "reasons": verdict.reasons,
    }


def _run_dedup(case: Case) -> dict:
    courses = [_candidate(item) for item in case.input["courses"]]
    kept = dedup_node({"scraped_courses": courses})["deduplicated_courses"]
    return {"kept": len(kept), "kept_urls": [course.url for course in kept]}


def _run_planner(case: Case) -> dict:
    cache = [_candidate({"url": f"https://example.com/{i}"}) for i in range(case.input.get("cache_count", 0))]
    update = research_planner_node(
        _state(
            case,
            cache_candidates=cache,
            completed_queries=case.input.get("completed_queries", []),
            research_iteration=case.input.get("research_iteration", 0),
        )
    )
    plan = update["research_plan"]
    return {"queries": plan.search_queries, "use_cache_first": plan.use_cache_first}


def _run_route(case: Case) -> dict:
    valid = [_candidate({"url": f"https://example.com/{i}"}) for i in range(case.input.get("valid_count", 0))]
    plan = ResearchPlan(topic="python", min_valid_candidates=case.input.get("min_valid", 3))
    route = enough_valid(
        {
            "valid_courses": valid,
            "research_plan": plan,
            "research_iteration": case.input.get("research_iteration", 0),
        }
    )
    return {"route": route}


L1A_NODES = {
    "validator": _run_validator,
    "dedup": _run_dedup,
    "planner": _run_planner,
    "route": _run_route,
}


def run_l1a(case: Case) -> dict:
    return L1A_NODES[case.input["node"]](case)


@dataclass
class Segment:
    visited: list[str]
    inner: list[str]
    next: tuple[str, ...]
    values: dict
    published: int


@dataclass
class Trace:
    segments: list[Segment] = field(default_factory=list)
    history: list[tuple[tuple[str, ...], dict]] = field(default_factory=list)
    refusal: str | None = None
    after_refusal: Segment | None = None

    @property
    def final(self) -> Segment:
        return self.segments[-1]


def _config(case: Case, thread_id: str) -> RunnableConfig:
    return {
        "configurable": {"thread_id": thread_id, "max_review_rounds": 5},
        "recursion_limit": 60,
    }


async def _segment(graph, config: RunnableConfig, inputs: Any, published: list[dict]) -> Segment:
    visited: list[str] = []
    inner: list[str] = []
    async for namespace, chunk in graph.astream(inputs, config, stream_mode="updates", subgraphs=True):
        names = [name for name in chunk if not name.startswith("__")]
        (inner if namespace else visited).extend(names)
    snapshot = await graph.aget_state(config)
    return Segment(visited, inner, snapshot.next, dict(snapshot.values), len(published))


def _first_inputs(case: Case) -> dict:
    return {**_initial_state(case.input["query"]), "user_id": case.input.get("user_id", "eval-user")}


async def _drive(case: Case) -> Trace:
    published: list[dict] = []
    store = InMemoryOutboxStore()
    set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": published.append})))
    graph = build_graph(checkpointer=memory_saver())
    config = _config(case, f"eval-{case.id}-{uuid.uuid4()}")
    trace = Trace()
    steps: list[dict | None] = [None, *({"manager_feedback": text} for text in case.input.get("feedback", []))]
    for step in steps:
        if step is None:
            inputs: Any = _first_inputs(case)
        else:
            await graph.aupdate_state(config, step)
            inputs = None
        trace.segments.append(await _segment(graph, config, inputs, published))
    async for snapshot in graph.aget_state_history(config):
        trace.history.append((snapshot.next, dict(snapshot.values)))
    trace.history.reverse()
    return trace


def _forget_thread(url: str, thread_id: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
            conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (thread_id,))
        conn.execute("DELETE FROM run_threads WHERE thread_id = %s", (thread_id,))
        conn.execute("DELETE FROM pending_courses WHERE run_id = %s", (thread_id,))


async def _drive_guarded(case: Case) -> Trace:
    url = os.environ["TEST_DATABASE_URL"]
    owner = case.input.get("user_id", "eval-user")
    thread_id = f"eval-{case.id}-{uuid.uuid4()}"
    published: list[dict] = []
    store = InMemoryOutboxStore()
    set_gateway(InlineGateway(store, OutboxWorker(store, {"publish_digest": published.append})))
    config = _config(case, thread_id)
    trace = Trace()
    try:
        async with open_checkpointer(url) as saver:
            graph = build_graph(checkpointer=saver)
            register_thread(owner, thread_id)
            trace.segments.append(await _segment(graph, config, _first_inputs(case), published))
            if "stored_schema_version" in case.input:
                with psycopg.connect(url, autocommit=True) as conn:
                    conn.execute(
                        "UPDATE run_threads SET schema_version = %s WHERE thread_id = %s",
                        (case.input["stored_schema_version"], thread_id),
                    )
            try:
                authorize_thread(case.input["resume_as"], thread_id)
                await graph.aupdate_state(config, {"manager_feedback": case.input["feedback"][0]})
                trace.segments.append(await _segment(graph, config, None, published))
            except (ThreadAccessError, IncompatibleThreadError) as exc:
                trace.refusal = type(exc).__name__
            snapshot = await graph.aget_state(config)
            trace.after_refusal = Segment([], [], snapshot.next, dict(snapshot.values), len(published))
    finally:
        _forget_thread(url, thread_id)
    return trace


def run_l3(case: Case) -> Trace:
    guarded = "postgres" in case.requires
    with patch.dict(os.environ):
        for name in LIVE_ENV:
            os.environ.pop(name, None)
        if guarded:
            os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
        try:
            return asyncio.run((_drive_guarded if guarded else _drive)(case))
        finally:
            set_gateway(None)


RUNNERS = {"l1a": run_l1a, "l3": run_l3}
