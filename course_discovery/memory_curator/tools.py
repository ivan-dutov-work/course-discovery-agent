from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from course_discovery.domain.models import CourseCandidate, MemoryPatch, UserMemory

NOT_STORED_SCOPES = frozenset({"this_run", "not_a_preference"})
WRITABLE_FIELDS = frozenset(
    {
        "preferred_providers",
        "avoided_providers",
        "preferred_languages",
        "budget_preference",
        "certificate_importance",
        "preferred_level",
        "preferred_course_length",
        "rejected_course_urls",
        "completed_course_urls",
    }
)


class ReadProfile(BaseModel):
    """Return the user's stored preferences."""

    model_config = ConfigDict(extra="forbid")


class ReadRunEvents(BaseModel):
    """Return the courses shown in this run and whether the digest was published."""

    model_config = ConfigDict(extra="forbid")


class ProposePatch(BaseModel):
    """Propose one change to the stored profile, with the scope of the statement it comes from."""

    model_config = ConfigDict(extra="forbid")

    scope: str = Field(pattern=r"^(durable|this_run|not_a_preference|topic:.+)$")
    reason: str = Field(min_length=1)
    patch: MemoryPatch


class Finish(BaseModel):
    """End the session. Nothing is stored unless propose_patch was accepted."""

    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)


TOOLS: dict[str, type[BaseModel]] = {
    "read_profile": ReadProfile,
    "read_run_events": ReadRunEvents,
    "propose_patch": ProposePatch,
    "finish": Finish,
}


def tool_specs() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": model.__doc__,
                "parameters": model.model_json_schema(),
            },
        }
        for name, model in TOOLS.items()
    ]


def _describe(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())


def _unwritable(patch: MemoryPatch) -> set[str]:
    return (set(patch.set) | set(patch.add) | set(patch.remove)) - WRITABLE_FIELDS


def propose(args: dict[str, Any], *, profile_read: bool) -> tuple[dict | None, str]:
    try:
        proposal = ProposePatch.model_validate(args)
    except ValidationError as exc:
        return None, f"error: {_describe(exc)}"
    if not profile_read:
        return None, "error: call read_profile first"
    if proposal.scope in NOT_STORED_SCOPES:
        return None, f"ok: scope {proposal.scope} is not stored"
    patch = proposal.patch
    if patch.is_empty():
        return None, "error: empty patch; call finish instead"
    if blocked := _unwritable(patch):
        return None, f"error: fields not writable: {sorted(blocked)}"
    if proposal.scope != "durable" and (patch.set or patch.add or patch.remove):
        return None, "error: a topic scope allows notes only"
    scoped = patch.model_copy(
        update={"add_notes": [note.model_copy(update={"scope": proposal.scope}) for note in patch.add_notes]}
    )
    return scoped.model_dump(mode="json"), "ok: proposal recorded"


def merge_proposals(proposals: list[dict], run_id: str) -> MemoryPatch:
    merged: dict[str, Any] = {"set": {}, "add": {}, "remove": {}, "add_notes": []}
    learned_at = datetime.now(timezone.utc)
    for raw in proposals:
        patch = MemoryPatch.model_validate(raw)
        merged["set"].update(patch.set)
        for key in ("add", "remove"):
            for name, items in getattr(patch, key).items():
                merged[key].setdefault(name, []).extend(items)
        merged["add_notes"].extend(
            note.model_copy(update={"learned_at": learned_at, "source_run_id": run_id})
            for note in patch.add_notes
        )
    return MemoryPatch(**merged)


def profile_json(memory: UserMemory) -> str:
    return memory.model_dump_json()


def run_events_json(courses: list[CourseCandidate], published: bool) -> str:
    return json.dumps(
        {
            "outcome": "published" if published else "discarded",
            "courses": [
                {"title": course.title, "provider": course.provider, "url": course.url}
                for course in courses
            ],
        }
    )
