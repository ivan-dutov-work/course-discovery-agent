from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from course_discovery.domain.models import CandidateValidation, CourseCandidate, EvidenceItem
from course_discovery.research_agent.cache import nodes as cache_nodes
from course_discovery.research_agent.cache.repository import (
    discard_staged_courses,
    promote_staged_courses,
    stage_courses,
)

REPO = "course_discovery.research_agent.cache.repository"


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self):
        self.pending: dict[tuple[str, str], tuple[str, str | None]] = {}
        self.courses: dict[str, str] = {}

    @contextmanager
    def transaction(self):
        yield

    def commit(self):
        pass

    def execute(self, sql, params=()):
        if "INSERT INTO pending_courses" in sql:
            run_id, url, candidate, validation = params
            self.pending[(run_id, url)] = (candidate, validation)
        elif "SELECT candidate, validation FROM pending_courses" in sql:
            run_id, urls = params
            return _Rows(
                [
                    (json.loads(c), json.loads(v) if v else None)
                    for (rid, url), (c, v) in sorted(self.pending.items())
                    if rid == run_id and url in urls
                ]
            )
        elif "DELETE FROM pending_courses" in sql:
            self.pending = {k: v for k, v in self.pending.items() if k[0] != params[0]}
        elif "INSERT INTO courses" in sql:
            self.courses[params[0]] = params[1]
            return _Rows([(len(self.courses), [])])
        return _Rows([(1,)])


def _course(url: str, source: str = "tavily") -> CourseCandidate:
    return CourseCandidate(
        title=f"Course at {url}",
        url=url,
        provider="Example",
        source=source,
        evidence=[EvidenceItem(source_url=url, quote_or_summary="Free", supports=["free"])],
    )


def _validation(url: str) -> CandidateValidation:
    return CandidateValidation(url=url, status="valid")


class PendingCourseTests(unittest.TestCase):
    def setUp(self):
        self.conn = _FakeConn()

        @contextmanager
        def fake_connect():
            yield self.conn

        for target in (f"{REPO}.connect",):
            patcher = patch(target, fake_connect)
            patcher.start()
            self.addCleanup(patcher.stop)
        embed = patch(f"{REPO}.embed_texts", lambda texts: [[0.0] for _ in texts])
        embed.start()
        self.addCleanup(embed.stop)
        vec = patch(f"{REPO}.to_pgvector", lambda vector: "[0]")
        vec.start()
        self.addCleanup(vec.stop)

    def test_staging_never_touches_the_served_table(self):
        stage_courses("run-1", [_course("https://a.example/1")], [_validation("https://a.example/1")])

        self.assertEqual(len(self.conn.pending), 1)
        self.assertEqual(self.conn.courses, {})

    def test_staging_the_same_run_twice_keeps_one_row(self):
        for _ in range(2):
            stage_courses("run-1", [_course("https://a.example/1")], [])

        self.assertEqual(len(self.conn.pending), 1)

    def test_promotion_writes_only_approved_urls_and_clears_the_run(self):
        stage_courses(
            "run-1",
            [_course("https://a.example/1"), _course("https://a.example/2")],
            [_validation("https://a.example/1"), _validation("https://a.example/2")],
        )

        promoted = promote_staged_courses("run-1", ["https://a.example/1"])

        self.assertEqual(promoted, 1)
        self.assertEqual(list(self.conn.courses), ["https://a.example/1"])
        self.assertEqual(self.conn.pending, {})

    def test_promotion_ignores_another_runs_staged_rows(self):
        stage_courses("run-1", [_course("https://a.example/1")], [])
        stage_courses("run-2", [_course("https://a.example/1")], [])

        promote_staged_courses("run-1", ["https://a.example/1"])

        self.assertEqual(list(self.conn.pending), [("run-2", "https://a.example/1")])

    def test_discard_clears_the_run_and_promotes_nothing(self):
        stage_courses("run-1", [_course("https://a.example/1")], [])

        discard_staged_courses("run-1")

        self.assertEqual(self.conn.pending, {})
        self.assertEqual(self.conn.courses, {})


class PendingNodeTests(unittest.TestCase):
    def test_stage_node_skips_cache_sourced_courses(self):
        staged: list = []
        state = {
            "valid_courses": [_course("https://a.example/1"), _course("https://a.example/2", "cache")],
            "validation_results": [],
        }
        with patch.object(
            cache_nodes, "stage_courses", lambda run_id, courses, validations: staged.extend(courses)
        ):
            cache_nodes.course_cache_upsert_node(state)

        self.assertEqual([c.url for c in staged], ["https://a.example/1"])

    def test_stage_node_ignores_uncertain_courses(self):
        staged: list = []
        state = {
            "valid_courses": [],
            "uncertain_courses": [_course("https://a.example/3")],
            "validation_results": [],
        }
        with patch.object(
            cache_nodes, "stage_courses", lambda run_id, courses, validations: staged.extend(courses)
        ):
            cache_nodes.course_cache_upsert_node(state)

        self.assertEqual(staged, [])

    def test_promote_node_passes_the_approved_web_urls_for_this_run(self):
        calls: list = []
        state = {
            "valid_courses": [_course("https://a.example/1"), _course("https://a.example/2", "cache")]
        }
        config = {"configurable": {"thread_id": "run-9"}}
        with patch.object(
            cache_nodes, "promote_staged_courses", lambda run_id, urls: calls.append((run_id, urls)) or 1
        ):
            cache_nodes.promote_approved_courses_node(state, config)

        self.assertEqual(calls, [("run-9", ["https://a.example/1"])])

    def test_drop_node_discards_this_runs_staging(self):
        calls: list = []
        config = {"configurable": {"thread_id": "run-9"}}
        with patch.object(cache_nodes, "discard_staged_courses", calls.append):
            cache_nodes.drop_pending_courses_node({}, config)

        self.assertEqual(calls, ["run-9"])


if __name__ == "__main__":
    unittest.main()
