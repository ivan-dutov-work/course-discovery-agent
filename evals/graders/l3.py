from __future__ import annotations

from evals.cases import Case
from evals.graders import grader
from evals.runners import PUBLISH_ROUTES, Trace

GATE = ("await_human_review",)


def _after(visited: list[str], node: str) -> str | None:
    if node not in visited:
        return None
    index = visited.index(node) + 1
    return visited[index] if index < len(visited) else None


@grader("pauses_before_review")
def pauses_before_review(case: Case, trace: Trace) -> tuple[bool, str]:
    paused = trace.segments if not case.expect.get("ends") else trace.segments[:-1]
    for index, segment in enumerate(paused):
        if segment.next != GATE:
            return False, f"segment {index} stopped at {segment.next or 'END'}, not the review gate"
    return True, f"{len(paused)} segments paused at the gate"


@grader("nothing_published_before_approval")
def nothing_published_before_approval(case: Case, trace: Trace) -> tuple[bool, str]:
    for index, segment in enumerate(trace.segments[:-1]):
        if segment.published:
            return False, f"segment {index} published {segment.published} before the last feedback"
    return True, "no publish before the last feedback"


@grader("routes")
def routes(case: Case, trace: Trace) -> tuple[bool, str]:
    wanted = case.expect["routes"]
    segments = trace.segments[1:]
    if len(wanted) != len(segments):
        return False, f"{len(wanted)} routes expected for {len(segments)} feedback segments"
    for index, (action, segment) in enumerate(zip(wanted, segments)):
        got = _after(segment.visited, "interpret_review_feedback")
        if got != PUBLISH_ROUTES[action]:
            return False, f"feedback {index}: expected {action} to reach {PUBLISH_ROUTES[action]}, got {got}"
    return True, "every feedback took its expected route"


@grader("outcome")
def outcome(case: Case, trace: Trace) -> tuple[bool, str]:
    final = trace.final
    checks = {
        "published": final.published,
        "ends": not final.next,
        "discarded": bool(final.values.get("discard_reason")) or "discard_run" in final.visited,
        "feedback_rounds": len(final.values.get("feedback_history", [])),
    }
    wrong = {key: (want, checks[key]) for key, want in case.expect.items() if key in checks and checks[key] != want}
    if wrong:
        return False, "; ".join(f"{key}: expected {want!r}, got {got!r}" for key, (want, got) in wrong.items())
    return True, "outcome matches"


@grader("pass_stamp_follows_rounds")
def pass_stamp_follows_rounds(case: Case, trace: Trace) -> tuple[bool, str]:
    for index, segment in enumerate(trace.segments):
        if segment.next != GATE:
            continue
        stamp = segment.values.get("research_pass")
        rounds = len(segment.values.get("feedback_history", []))
        if stamp != rounds:
            return False, f"segment {index}: research_pass {stamp} but {rounds} review rounds"
        if "retry_research_pass" in segment.visited:
            return False, f"segment {index}: stale result retried"
    return True, "research_pass equals the review round at every pause"


@grader("digest_present_at_pause")
def digest_present_at_pause(case: Case, trace: Trace) -> tuple[bool, str]:
    for index, segment in enumerate(trace.segments):
        if segment.next == GATE and not segment.values.get("digest"):
            return False, f"segment {index} paused with no digest"
    return True, "every pause carries a digest"


@grader("resume_refused")
def resume_refused(case: Case, trace: Trace) -> tuple[bool, str]:
    wanted = case.expect["refusal"]
    if trace.refusal != wanted:
        return False, f"expected {wanted}, got {trace.refusal or 'a resume that went through'}"
    return True, f"resume refused with {wanted}"


@grader("refusal_changes_nothing")
def refusal_changes_nothing(case: Case, trace: Trace) -> tuple[bool, str]:
    after = trace.after_refusal
    if after is None:
        return False, "no state read after the refused resume"
    if after.next != GATE:
        return False, f"thread moved to {after.next or 'END'}"
    if after.published:
        return False, f"{after.published} publishes after a refused resume"
    if after.values.get("manager_feedback") or after.values.get("feedback_history"):
        return False, "refused feedback reached the state"
    if after.values != trace.final.values:
        return False, "state differs from the paused state"
    return True, "thread still parked at the gate with its original state"

