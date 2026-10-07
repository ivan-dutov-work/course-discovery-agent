from __future__ import annotations

from typing import Protocol

from course_discovery.conversation.selector import ParkedThread, SelectorResult, ThreadSelector


class ThreadLookup(Protocol):
    def latest_parked(self, user_id: str) -> ParkedThread | None: ...


def select_for_user(
    selector: ThreadSelector, lookup: ThreadLookup, user_id: str, message: str
) -> SelectorResult:
    return selector.select(message, lookup.latest_parked(user_id))
