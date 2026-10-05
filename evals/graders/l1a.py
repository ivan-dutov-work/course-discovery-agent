from __future__ import annotations

from evals.cases import Case
from evals.graders import grader


@grader("exact")
def exact(case: Case, result: dict) -> tuple[bool, str]:
    wrong = {
        key: (want, result.get(key))
        for key, want in case.expect.items()
        if result.get(key) != want
    }
    if wrong:
        return False, "; ".join(f"{key}: expected {want!r}, got {got!r}" for key, (want, got) in wrong.items())
    return True, "all fields match"


@grader("evidence_rule")
def evidence_rule(case: Case, result: dict) -> tuple[bool, str]:
    if result["status"] == "valid" and result["missing_evidence"]:
        return False, f"valid with missing evidence {result['missing_evidence']}"
    return True, "no valid verdict with missing evidence"
