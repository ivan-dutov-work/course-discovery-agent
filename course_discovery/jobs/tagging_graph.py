from __future__ import annotations

from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from course_discovery.guardrails.injection import InjectionAction, screen_untrusted
from course_discovery.jobs.tagging import TopicTagger
from course_discovery.persistence.postgres import open_connection
from course_discovery.research_agent.tagging.vocabulary import TOPIC_VOCABULARY

_ALLOWED = frozenset(TOPIC_VOCABULARY)


class TagState(TypedDict, total=False):
    course_id: int
    content_hash: str
    title: str
    description: str
    action: str
    topics: list[str]
    outcome: str


def build_tagging_graph(database_url: str, tagger: TopicTagger, checkpointer=None):
    def load_course(state: TagState) -> dict:
        with open_connection(database_url) as conn:
            row = conn.execute(
                "SELECT title, coalesce(description, ''), content_hash FROM courses WHERE id = %s",
                (state["course_id"],),
            ).fetchone()
        if row is None or row[2] != state["content_hash"]:
            return {"outcome": "stale"}
        return {"title": row[0], "description": row[1]}

    def screen_text(state: TagState) -> dict:
        action = screen_untrusted(f"{state['title']}\n{state['description']}")
        return {"action": action.value}

    def tag_text(state: TagState) -> dict:
        topics = [t for t in tagger.tag(state["title"], state["description"]) if t in _ALLOWED]
        return {"topics": list(dict.fromkeys(topics))}

    def write_topics(state: TagState) -> dict:
        _record(database_url, state, "tagged", state["topics"])
        return {"outcome": "tagged"}

    def withhold(state: TagState) -> dict:
        _record(database_url, state, "withheld", None)
        return {"outcome": "withheld"}

    def after_load(state: TagState) -> str:
        return END if state.get("outcome") == "stale" else "screen_text"

    def after_screen(state: TagState) -> str:
        return "tag_text" if state["action"] == InjectionAction.PASS.value else "withhold"

    builder = StateGraph(TagState)
    builder.add_node("load_course", load_course)
    builder.add_node("screen_text", screen_text)
    builder.add_node("tag_text", tag_text)
    builder.add_node("write_topics", write_topics)
    builder.add_node("withhold", withhold)
    builder.add_edge(START, "load_course")
    builder.add_conditional_edges("load_course", after_load, ["screen_text", END])
    builder.add_conditional_edges("screen_text", after_screen, ["tag_text", "withhold"])
    builder.add_edge("tag_text", "write_topics")
    builder.add_edge("write_topics", END)
    builder.add_edge("withhold", END)
    return builder.compile(checkpointer=checkpointer)


def _record(database_url: str, state: TagState, outcome: str, topics: list[str] | None) -> None:
    with open_connection(database_url) as conn:
        conn.execute(
            """
            UPDATE courses
            SET topics = coalesce(%s, topics), tagged_content_hash = %s, tagging_outcome = %s
            WHERE id = %s AND content_hash = %s
            """,
            (topics, state["content_hash"], outcome, state["course_id"], state["content_hash"]),
        )
        conn.commit()
