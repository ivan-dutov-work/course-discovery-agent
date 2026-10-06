from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

import psycopg

from course_discovery.effects.gateway import OutboxGateway
from course_discovery.effects.postgres_store import PostgresOutboxStore
from course_discovery.effects.worker import OutboxWorker
from course_discovery.guardrails.injection import set_injection_screen
from course_discovery.jobs.tagging import TAG_COURSE, KeywordTagger, schedule_tagging, tagging_handler
from tests.injection_samples import INJECTIONS

ROOT = Path(__file__).parent.parent
CHILD = Path(__file__).parent / "kill_child_tagging.py"
COURSES = 200


class CountingTagger:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()

    def tag(self, title: str, description: str) -> list[str]:
        with self._lock:
            self.calls.append((title, description))
        return ["python"]


class MarkerScreen:
    def score(self, text: str) -> float:
        return 0.95 if any(sample in text for sample in INJECTIONS.values()) else 0.0


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class TaggingJobTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        self.prefix = f"https://tagjob.test/{uuid.uuid4().hex}/"
        self.store = PostgresOutboxStore(self.url)
        self.gateway = OutboxGateway(self.store)
        set_injection_screen(MarkerScreen())
        self.addCleanup(set_injection_screen, None)
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        with psycopg.connect(self.url, autocommit=True) as conn:
            ids = [r[0] for r in conn.execute("SELECT id FROM courses WHERE canonical_url LIKE %s", (self.prefix + "%",))]
            conn.execute("DELETE FROM outbox WHERE kind = %s AND (payload ->> 'course_id')::bigint = ANY(%s)", (TAG_COURSE, ids))
            conn.execute("DELETE FROM courses WHERE canonical_url LIKE %s", (self.prefix + "%",))

    def _seed(self, count: int, description=lambda i: f"Learn python basics, lesson {i}") -> None:
        with psycopg.connect(self.url, autocommit=True) as conn:
            for i in range(count):
                conn.execute(
                    "INSERT INTO courses (canonical_url, title, description) VALUES (%s, %s, %s)",
                    (f"{self.prefix}{i}", f"Course {i}", description(i)),
                )

    def _scalar(self, sql: str, *params):
        with psycopg.connect(self.url) as conn:
            return conn.execute(sql, params).fetchone()[0]

    def _tagged(self) -> int:
        return self._scalar(
            "SELECT count(*) FROM courses WHERE canonical_url LIKE %s AND tagged_content_hash = content_hash",
            self.prefix + "%",
        )

    def _worker(self, tagger, **kwargs) -> OutboxWorker:
        return OutboxWorker(self.store, {TAG_COURSE: tagging_handler(self.url, tagger)}, batch_size=10, **kwargs)

    def _drain(self, tagger, workers: int = 1) -> None:
        def loop(worker):
            while worker.run_once().delivered:
                pass

        threads = [threading.Thread(target=loop, args=(self._worker(tagger),)) for _ in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

    def _schedule(self) -> int:
        return schedule_tagging(self.url, self.gateway, url_prefix=self.prefix)

    def test_two_concurrent_schedulers_and_three_workers_tag_each_course_once(self):
        self._seed(COURSES)
        submitted: list[int] = []
        threads = [threading.Thread(target=lambda: submitted.append(self._schedule())) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(
            self._scalar(
                "SELECT count(*) FROM outbox WHERE kind = %s AND (payload ->> 'course_id')::bigint IN "
                "(SELECT id FROM courses WHERE canonical_url LIKE %s)",
                TAG_COURSE,
                self.prefix + "%",
            ),
            COURSES,
        )
        tagger = CountingTagger()
        self._drain(tagger, workers=3)
        self.assertEqual(len(tagger.calls), COURSES)
        self.assertEqual(len({title for title, _ in tagger.calls}), COURSES)
        self.assertEqual(self._tagged(), COURSES)
        self.assertEqual(self._schedule(), 0)

    def test_changed_text_is_tagged_again_exactly_once(self):
        self._seed(5)
        self._schedule()
        self._drain(CountingTagger())
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute(
                "UPDATE courses SET description = 'Now about sql and databases' WHERE canonical_url = %s",
                (self.prefix + "3",),
            )
        self.assertEqual(self._schedule(), 1)
        tagger = CountingTagger()
        self._drain(tagger)
        self.assertEqual([title for title, _ in tagger.calls], ["Course 3"])
        self.assertEqual(self._tagged(), 5)

    def test_stale_effect_does_not_overwrite_newer_text(self):
        self._seed(1)
        self._schedule()
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("UPDATE courses SET description = 'rewritten' WHERE canonical_url = %s", (self.prefix + "0",))
        tagger = CountingTagger()
        self._drain(tagger)
        self.assertEqual(tagger.calls, [])
        self.assertEqual(self._tagged(), 0)

    def test_injected_description_never_reaches_the_tagger(self):
        self._seed(3, description=lambda i: INJECTIONS["blunt"] if i == 1 else "python lessons")
        self._schedule()
        tagger = CountingTagger()
        self._drain(tagger)
        self.assertEqual(len(tagger.calls), 2)
        self.assertTrue(all(INJECTIONS["blunt"] not in description for _, description in tagger.calls))
        self.assertEqual(
            self._scalar("SELECT tagging_outcome FROM courses WHERE canonical_url = %s", self.prefix + "1"),
            "withheld",
        )
        self.assertEqual(self._schedule(), 0)

    def test_courses_with_existing_topics_are_left_alone(self):
        self._seed(2)
        with psycopg.connect(self.url, autocommit=True) as conn:
            conn.execute("UPDATE courses SET topics = ARRAY['python'] WHERE canonical_url = %s", (self.prefix + "0",))
        self.assertEqual(self._schedule(), 1)

    def test_sigkilled_worker_leaves_every_course_tagged_with_at_most_one_rerun(self):
        self._seed(COURSES)
        self._schedule()
        log = Path(os.environ.get("TMPDIR", "/tmp")) / f"tagging-{uuid.uuid4().hex}.log"
        self.addCleanup(lambda: log.unlink(missing_ok=True))
        child = subprocess.run(
            [sys.executable, str(CHILD), str(log), "60"],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(child.returncode, -9, child.stderr[-500:])
        before = len(log.read_text().splitlines())
        self.assertEqual(before, 60)
        self.assertLess(self._tagged(), COURSES)

        time.sleep(1.3)
        tagger = CountingTagger()
        self._drain(tagger)
        self.assertEqual(self._tagged(), COURSES)
        self.assertLessEqual(before + len(tagger.calls), COURSES + 1)
        self.assertGreaterEqual(before + len(tagger.calls), COURSES)

    def test_keyword_tagger_matches_vocabulary_terms(self):
        self.assertEqual(KeywordTagger().tag("Intro", "Python and machine learning with SQL"), ["machine-learning", "python", "sql"])


if __name__ == "__main__":
    unittest.main()
