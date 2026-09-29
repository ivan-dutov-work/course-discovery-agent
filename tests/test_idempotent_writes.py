from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import patch

from course_discovery.domain.models import CourseCandidate, EvidenceItem
from course_discovery.research_agent.cache.repository import upsert_courses
from course_discovery.research_agent.memory.repository import record_feedback


class _Result:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self):
        self.events: dict[str, tuple] = {}
        self.evidence: set[tuple] = set()

    @contextmanager
    def transaction(self):
        yield

    def commit(self):
        pass

    def execute(self, sql, params=()):
        if "INSERT INTO recommendation_events" in sql:
            if "ON CONFLICT (idempotency_key) DO NOTHING" in sql:
                self.events.setdefault(params[-1], params)
            else:
                self.events[object()] = params
        elif "INSERT INTO course_evidence" in sql:
            row = (params[0], params[1], params[2])
            if "ON CONFLICT (course_id, source_url, quote_or_summary) DO NOTHING" not in sql:
                row += (object(),)
            self.evidence.add(row)
        return _Result((1,))


def _course() -> CourseCandidate:
    return CourseCandidate(
        title="Python 101",
        url="https://example.com/python-101",
        provider="Example",
        source="tavily",
        evidence=[
            EvidenceItem(
                source_url="https://example.com/python-101",
                quote_or_summary="Free with certificate",
                supports=["free"],
            )
        ],
    )


class IdempotentWriteTests(unittest.TestCase):
    def test_record_feedback_replay_inserts_once(self):
        conn = _FakeConn()

        @contextmanager
        def fake_connect():
            yield conn

        with patch("course_discovery.research_agent.memory.repository.connect", fake_connect):
            for _ in range(2):
                record_feedback(
                    "u1", [_course()], "python", accepted=True, feedback_text=None, run_id="run-1"
                )

        self.assertEqual(len(conn.events), 1)

    def test_upsert_courses_replay_inserts_evidence_once(self):
        conn = _FakeConn()

        @contextmanager
        def fake_connect():
            yield conn

        with patch("course_discovery.research_agent.cache.repository.connect", fake_connect):
            for _ in range(2):
                upsert_courses([_course()], [])

        self.assertEqual(len(conn.evidence), 1)


if __name__ == "__main__":
    unittest.main()
