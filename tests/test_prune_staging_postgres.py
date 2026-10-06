from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import psycopg

from course_discovery.research_agent.cache.repository import prune_staged_courses

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class PruneStagedCoursesTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        env.start()
        self.addCleanup(env.stop)
        self.conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.conn.close)
        self.conn.execute("TRUNCATE pending_courses, run_threads")

    def _stage(self, run_id: str, age_days: int) -> None:
        self.conn.execute(
            """
            INSERT INTO pending_courses (run_id, canonical_url, candidate, created_at)
            VALUES (%s, 'https://example.com/a', '{}'::jsonb, now() - make_interval(days => %s))
            """,
            (run_id, age_days),
        )

    def _register(self, run_id: str, idle_days: int) -> None:
        self.conn.execute(
            """
            INSERT INTO run_threads (thread_id, user_id, last_activity_at)
            VALUES (%s, 'u', now() - make_interval(days => %s))
            """,
            (run_id, idle_days),
        )

    def _runs(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT run_id FROM pending_courses")}

    def test_removes_only_old_rows_of_idle_runs(self):
        self._stage("old-idle", 40)
        self._register("old-idle", 40)
        self._stage("old-unregistered", 40)
        self._stage("old-but-active", 40)
        self._register("old-but-active", 1)
        self._stage("fresh", 1)
        self._register("fresh", 1)

        self.assertEqual(prune_staged_courses(30), 2)
        self.assertEqual(self._runs(), {"old-but-active", "fresh"})

    def test_nothing_to_prune(self):
        self._stage("fresh", 1)
        self.assertEqual(prune_staged_courses(30), 0)
        self.assertEqual(self._runs(), {"fresh"})


if __name__ == "__main__":
    unittest.main()
