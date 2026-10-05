from __future__ import annotations

import json
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from course_discovery.app.prompts import TAGGER_SYSTEM_PROMPT
from course_discovery.research_agent.tagging.vocabulary import TOPIC_VOCABULARY

_ALLOWED = frozenset(TOPIC_VOCABULARY)
MAX_TAGS_PER_MODULE = 3


@dataclass(frozen=True)
class CourseForTagging:
    title: str
    description: str | None
    modules: tuple[str, ...]


class ModuleTags(BaseModel):
    module: str
    tags: list[str] = Field(default_factory=list)


class CourseTagging(BaseModel):
    modules: list[ModuleTags]


def build_tag_messages(course: CourseForTagging) -> list:
    payload = json.dumps(
        {
            "title": course.title,
            "description": course.description or "",
            "modules": list(course.modules),
        },
        ensure_ascii=False,
    )
    return [SystemMessage(content=TAGGER_SYSTEM_PROMPT), HumanMessage(content=payload)]


def clean_tagging(course: CourseForTagging, raw: CourseTagging) -> list[ModuleTags]:
    by_module = {entry.module: entry for entry in raw.modules}
    cleaned = []
    for module in course.modules:
        entry = by_module.get(module)
        tags = []
        for tag in entry.tags if entry else []:
            if tag in _ALLOWED and tag not in tags:
                tags.append(tag)
        cleaned.append(ModuleTags(module=module, tags=tags[:MAX_TAGS_PER_MODULE]))
    return cleaned


def tag_course(llm, course: CourseForTagging, *, config: dict | None = None) -> list[ModuleTags]:
    structured = llm.with_structured_output(CourseTagging)
    raw = structured.invoke(build_tag_messages(course), config=config)
    return clean_tagging(course, raw)
