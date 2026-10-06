from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from evals.cases import Case


@dataclass(frozen=True)
class Verdict:
    grader: str
    passed: bool
    reason: str


Grader = Callable[[Case, Any], "tuple[bool, str]"]
GRADERS: dict[str, Grader] = {}


def grader(name: str) -> Callable[[Grader], Grader]:
    def register(fn: Grader) -> Grader:
        if name in GRADERS:
            raise ValueError(f"grader {name} registered twice")
        GRADERS[name] = fn
        return fn

    return register


def grade(case: Case, result: Any) -> list[Verdict]:
    import evals.graders.l1a  # noqa: F401
    import evals.graders.l3  # noqa: F401

    verdicts = []
    for name in case.graders:
        passed, reason = GRADERS[name](case, result)
        verdicts.append(Verdict(name, passed, reason))
    return verdicts
