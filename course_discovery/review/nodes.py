from __future__ import annotations

from langchain_core.runnables import RunnableConfig

from course_discovery.domain.run_config import chat_mode, run_id_of
from course_discovery.domain.state import AgentState
from course_discovery.effects.factory import get_gateway
from course_discovery.effects.handlers import SEND_DIGEST_MESSAGE, SEND_FEEDBACK_PROMPT
from course_discovery.effects.models import Effect

FEEDBACK_PROMPT = "Tell me what to change, or just ask for something new."


def review_gate_node(state: AgentState) -> dict:
    return {}


def send_review_digest_node(state: AgentState, config: RunnableConfig) -> dict:
    if not chat_mode(config):
        return {}
    run_id = run_id_of(config)
    gateway = get_gateway()
    gateway.submit(
        Effect(
            key=f"digest:{run_id}:{state.get('research_pass', 0)}",
            kind=SEND_DIGEST_MESSAGE,
            payload={
                "run_id": run_id,
                "user_id": state.get("user_id"),
                "research_pass": state.get("research_pass", 0),
                "text": state.get("digest") or "",
                "course_urls": [course.url for course in state.get("valid_courses", [])],
            },
        )
    )
    gateway.submit(
        Effect(
            key=f"feedback_prompt:{run_id}",
            kind=SEND_FEEDBACK_PROMPT,
            payload={"run_id": run_id, "user_id": state.get("user_id"), "text": FEEDBACK_PROMPT},
        )
    )
    return {}
