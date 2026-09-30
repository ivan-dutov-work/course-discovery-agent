from __future__ import annotations

from contextlib import ExitStack
from typing import Callable
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage

from course_discovery.domain.models import MemoryPatch, UserMemory
from course_discovery.memory_curator import graph as curator_graph
from course_discovery.research_agent.memory.repository import apply_patch, redact_patch


def call(name: str, **args) -> dict:
    return {"name": name, "args": args, "id": f"call-{name}"}


def reply(*calls: dict) -> AIMessage:
    return AIMessage(content="", tool_calls=list(calls))


class ScriptedModel:
    def __init__(self, script: list | Callable[[list, int], AIMessage]):
        self.script = script
        self.seen: list[list] = []

    def bind_tools(self, tools) -> "ScriptedModel":
        return self

    def invoke(self, messages) -> AIMessage:
        turn = len(self.seen)
        self.seen.append(list(messages))
        if callable(self.script):
            return self.script(messages, turn)
        step = self.script[turn]
        if isinstance(step, Exception):
            raise step
        return step


class FakeProfiles:
    def __init__(self):
        self.memories: dict[str, UserMemory] = {}
        self.claimed: set[str] = set()
        self.saves: list[tuple[str, MemoryPatch]] = []
        self.feedback_calls: list[dict] = []

    def load(self, user_id):
        return self.memories.get(user_id, UserMemory())

    def exists(self, run_id):
        return run_id in self.claimed

    def save(self, user_id, patch, *, run_id=None):
        if run_id is not None:
            if run_id in self.claimed:
                return None
            self.claimed.add(run_id)
        patch = redact_patch(patch)
        self.saves.append((user_id, patch))
        self.memories[user_id] = apply_patch(self.load(user_id), patch)
        return self.memories[user_id]

    def record_feedback(self, user_id, courses, query, **kwargs):
        self.feedback_calls.append({"user_id": user_id, "count": len(courses), **kwargs})


def install_curator(test, model: ScriptedModel | None, profiles: FakeProfiles | None = None) -> MagicMock:
    stack = ExitStack()
    test.addCleanup(stack.close)
    degradation = MagicMock()
    patches = {
        "llm_enabled": lambda: True,
        "build_llm": lambda *a, **k: model,
        "record_degradation": degradation,
    }
    if profiles is not None:
        patches.update(
            load_user_memory=profiles.load,
            memory_update_exists=profiles.exists,
            save_user_memory=profiles.save,
        )
    for name, value in patches.items():
        stack.enter_context(patch.object(curator_graph, name, value))
    return degradation
