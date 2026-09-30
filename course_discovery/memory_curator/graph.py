from __future__ import annotations

import operator
import os
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages

from course_discovery.app.llm import build_llm, llm_enabled
from course_discovery.app.prompts import CURATOR_SYSTEM_PROMPT
from course_discovery.domain.models import CourseCandidate, DeliveryStatus
from course_discovery.guardrails import redact_pii
from course_discovery.memory_curator.tools import (
    TOOLS,
    merge_proposals,
    profile_json,
    propose,
    run_events_json,
    tool_specs,
)
from course_discovery.observability.logging import get_logger, sanitize_error
from course_discovery.observability.metrics import record_degradation
from course_discovery.research_agent.memory.repository import (
    load_user_memory,
    memory_update_exists,
    save_user_memory,
)
from course_discovery.resilience import transient_retry
from course_discovery.review.router import APPROVAL_PHRASES

logger = get_logger(__name__)

MAX_CURATOR_STEPS = int(os.getenv("MAX_CURATOR_STEPS", "4"))


class CuratorInput(TypedDict):
    user_id: str | None
    run_id: str
    feedback_history: list[str]
    valid_courses: list[CourseCandidate]
    publish_status: DeliveryStatus | None


class CuratorOutput(TypedDict):
    memory_update: str | None


class CuratorState(CuratorInput, CuratorOutput):
    messages: Annotated[list[AnyMessage], add_messages]
    steps: int
    profile_read: bool
    finished: bool
    failure: str | None
    proposals: Annotated[list[dict], operator.add]


def _published(state: CuratorInput) -> bool:
    return state.get("publish_status") in {DeliveryStatus.QUEUED, DeliveryStatus.DELIVERED}


def load_context(state: CuratorState) -> dict:
    lines = (redact_pii(item) or "" for item in state.get("feedback_history") or [])
    feedback = [line for line in lines if line.strip() and line.strip().lower() not in APPROVAL_PHRASES]
    if not state.get("user_id") or not feedback:
        return {"memory_update": "skipped:no_feedback"}
    if not llm_enabled():
        return {"memory_update": "skipped:no_llm"}
    if memory_update_exists(state["run_id"]):
        return {"memory_update": "skipped:already_applied"}
    numbered = "\n".join(f"{i}. {line}" for i, line in enumerate(feedback, start=1))
    outcome = "published" if _published(state) else "discarded"
    return {
        "messages": [
            SystemMessage(content=CURATOR_SYSTEM_PROMPT),
            HumanMessage(content=f"Run outcome: {outcome}\nReview feedback, oldest first:\n{numbered}"),
        ],
        "steps": 0,
        "profile_read": False,
        "finished": False,
        "failure": None,
        "proposals": [],
    }


def _after_context(state: CuratorState) -> str:
    return END if state.get("memory_update") else "curator_model"


def curator_model(state: CuratorState) -> dict:
    steps = state["steps"] + 1
    try:
        reply = build_llm("curator", max_retries=2).bind_tools(tool_specs()).invoke(state["messages"])
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "curator_llm_error",
            extra={"event": "curator.llm_error", "run_id": state["run_id"], **sanitize_error(exc)},
        )
        return {"steps": steps, "failure": "llm_error"}
    return {"messages": [reply], "steps": steps}


def _after_model(state: CuratorState) -> str:
    return "commit" if state.get("failure") else "run_tools"


def _call_tool(state: CuratorState, name: str, args: dict, update: dict) -> str:
    if name not in TOOLS:
        return f"error: unknown tool {name}"
    if name == "read_profile":
        update["profile_read"] = True
        return profile_json(load_user_memory(state["user_id"]))
    if name == "read_run_events":
        return run_events_json(state.get("valid_courses") or [], _published(state))
    if name == "finish":
        update["finished"] = True
        return "ok"
    patch, message = propose(args, profile_read=state["profile_read"] or update.get("profile_read", False))
    if patch is not None:
        update["proposals"].append(patch)
    return message


def run_tools(state: CuratorState) -> dict:
    last = state["messages"][-1]
    calls = last.tool_calls if isinstance(last, AIMessage) else []
    if not calls:
        return {"failure": "no_tool_call"}
    update: dict = {"proposals": []}
    replies = [
        ToolMessage(content=_call_tool(state, call["name"], call["args"], update), tool_call_id=call["id"])
        for call in calls
    ]
    return {**update, "messages": replies}


def _after_tools(state: CuratorState) -> str:
    if state.get("failure") or state.get("finished") or state["steps"] >= MAX_CURATOR_STEPS:
        return "commit"
    return "curator_model"


def commit(state: CuratorState) -> dict:
    failure = state.get("failure") or (None if state.get("finished") else "step_cap")
    if failure:
        record_degradation("curator", failure)
        return {"memory_update": f"failed:{failure}"}
    patch = merge_proposals(state.get("proposals") or [], state["run_id"])
    if patch.is_empty():
        return {"memory_update": "skipped:no_changes"}
    applied = save_user_memory(state["user_id"], patch, run_id=state["run_id"])
    return {"memory_update": "committed" if applied else "skipped:not_applied"}


def build_curator_graph():
    builder = StateGraph(CuratorState, input_schema=CuratorInput, output_schema=CuratorOutput)
    builder.add_node("load_context", load_context)
    builder.add_node("curator_model", curator_model)
    builder.add_node("run_tools", run_tools)
    builder.add_node("commit", commit, retry_policy=transient_retry())

    builder.set_entry_point("load_context")
    builder.add_conditional_edges("load_context", _after_context, {END: END, "curator_model": "curator_model"})
    builder.add_conditional_edges("curator_model", _after_model, {"commit": "commit", "run_tools": "run_tools"})
    builder.add_conditional_edges("run_tools", _after_tools, {"commit": "commit", "curator_model": "curator_model"})
    builder.add_edge("commit", END)
    return builder.compile()
