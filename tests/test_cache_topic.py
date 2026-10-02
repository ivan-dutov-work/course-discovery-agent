from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import psycopg

from course_discovery.domain.models import (
    CandidateValidation,
    CourseCandidate,
    SearchFilters,
    UserMemory,
)
from course_discovery.research_agent.cache.nodes import course_cache_lookup_node
from course_discovery.research_agent.cache.repository import search_course_cache, upsert_courses
from course_discovery.research_agent.embeddings import EmbeddingError, HashingEmbedder, set_embedder
from course_discovery.research_agent.embeddings.__main__ import backfill
from course_discovery.research_agent.planning.nodes import research_planner_node
from tests.test_embeddings import BrokenEmbedder

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

COURSES = {
    "py1": ("Python for Everybody", "Beginner Python specialization with assignments.", 0.80),
    "py2": ("CS50's Introduction to Programming with Python", "Harvard Python course.", 0.90),
    "py3": ("Real Python Advanced Tutorials", "Deep dives into Python internals.", 0.85),
    "js1": ("The Complete JavaScript Course", "Modern JavaScript from scratch.", 0.95),
    "sql1": ("SQL for Data Analysis", "Query databases with SQL.", 0.90),
}


def _candidate(key: str) -> CourseCandidate:
    title, description, confidence = COURSES[key]
    return CourseCandidate(
        title=title,
        provider="coursera",
        url=f"https://example.com/{key}",
        description=description,
        level="beginner",
        language="en",
        is_free=True,
        has_certificate=True,
        source="cache",
        confidence=confidence,
    )


def _filters(topic: str, **kwargs) -> SearchFilters:
    return SearchFilters(topic=topic, **kwargs)


class FixedEmbedder:
    dimension = 1536
    topic_floor = 0.1

    def __init__(self, relative_cutoff: float) -> None:
        self.relative_cutoff = relative_cutoff

    def embed(self, texts):
        vectors = []
        for text in texts:
            head = [1.0, 0.0] if text == "python" else [0.0, 1.0]
            if text.startswith("Python for Everybody"):
                head = [0.8, 0.6]
            elif text.startswith("CS50"):
                head = [0.5, 0.866]
            vectors.append(head + [0.0] * 1534)
        return vectors


class SeedCacheTopicTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DATABASE_URL", None)
        set_embedder(HashingEmbedder())
        self.addCleanup(set_embedder, None)

    def _titles(self, topic: str, memory: UserMemory | None = None) -> list[str]:
        return [c.title for c in search_course_cache(_filters(topic), memory or UserMemory())]

    def test_python_query_returns_python_courses(self):
        titles = self._titles("python")
        self.assertEqual(len(titles), 2)
        self.assertTrue(all("Python" in title for title in titles))

    def test_unrelated_topics_return_nothing(self):
        for topic in ["cooking", "javascript", "quantum physics", "machine learning"]:
            self.assertEqual(self._titles(topic), [], topic)

    def test_floor_follows_the_embedder(self):
        from course_discovery.research_agent.cache.scoring import MIN_TOPIC_SIMILARITY, topic_floor

        class Fake:
            dimension = 1536
            topic_floor = 0.4

        class Bare:
            dimension = 1536

        set_embedder(Fake())
        self.assertEqual(topic_floor(), 0.4)
        set_embedder(Bare())
        self.assertEqual(topic_floor(), MIN_TOPIC_SIMILARITY)

    def test_relative_cutoff_drops_courses_far_below_the_best(self):
        set_embedder(FixedEmbedder(relative_cutoff=0.7))
        self.assertEqual(self._titles("python"), ["Python for Everybody"])
        set_embedder(FixedEmbedder(relative_cutoff=0.5))
        self.assertEqual(len(self._titles("python")), 2)
        set_embedder(FixedEmbedder(relative_cutoff=0.0))
        self.assertEqual(len(self._titles("python")), 2)

    def test_blank_topic_never_reaches_the_embedder(self):
        class RejectsEmptyInput:
            dimension = 1536

            def embed(self, texts):
                if any(not text.strip() for text in texts):
                    raise EmbeddingError("empty input")
                return [[1.0] + [0.0] * 1535 for _ in texts]

        set_embedder(RejectsEmptyInput())
        for topic in ["", "   "]:
            self.assertGreaterEqual(len(self._titles(topic)), 0, topic)

    def test_query_with_facet_words_still_matches_on_topic_only(self):
        self.assertEqual(len(self._titles("Find free Python certificate beginners")), 2)
        self.assertEqual(self._titles("free certificate beginners cooking"), [])

    def test_topic_without_content_words_skips_the_topic_filter(self):
        self.assertEqual(len(self._titles("free beginner")), 2)

    def test_completed_rejected_and_avoided_are_excluded(self):
        every = search_course_cache(_filters("python"), UserMemory())
        first, second = every[0], every[1]
        self.assertEqual(
            self._titles("python", UserMemory(completed_course_urls=[first.url])), [second.title]
        )
        self.assertEqual(
            self._titles("python", UserMemory(rejected_course_urls=[second.url])), [first.title]
        )
        self.assertEqual(
            self._titles("python", UserMemory(avoided_providers=[first.provider or ""])),
            [] if first.provider == second.provider else [second.title],
        )

    def test_ordering_is_stable_and_follows_similarity(self):
        first = self._titles("python")
        self.assertEqual(first, self._titles("python"))
        self.assertEqual(first[0], "Python for Everybody")
        self.assertEqual(self._titles("python programming")[0], "CS50's Introduction to Programming with Python")

    def test_embedder_failure_propagates(self):
        set_embedder(BrokenEmbedder())
        with self.assertRaises(ConnectionError):
            search_course_cache(_filters("python"), UserMemory())

    def test_unrelated_topic_makes_the_planner_dispatch_web_search(self):
        state = {"search_filters": _filters("cooking"), "user_memory": UserMemory()}
        state.update(course_cache_lookup_node(state))
        plan = research_planner_node(state)["research_plan"]
        self.assertEqual(state["cache_candidates"], [])
        self.assertTrue(plan.search_queries)


@unittest.skipUnless(TEST_DATABASE_URL, "TEST_DATABASE_URL not set")
class PostgresCacheTopicTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {"DATABASE_URL": TEST_DATABASE_URL})
        env.start()
        self.addCleanup(env.stop)
        set_embedder(HashingEmbedder())
        self.addCleanup(set_embedder, None)
        self.conn = psycopg.connect(TEST_DATABASE_URL, autocommit=True)
        self.addCleanup(self.conn.close)
        self.conn.execute("TRUNCATE recommendation_events, course_evidence, courses, users CASCADE")

    def _store(self, keys, status="valid"):
        courses = [_candidate(k) for k in keys]
        upsert_courses(courses, [CandidateValidation(url=c.url, status=status) for c in courses])

    def _titles(self, topic: str, memory: UserMemory | None = None, **filters) -> list[str]:
        found = search_course_cache(_filters(topic, **filters), memory or UserMemory())
        return [c.title for c in found]

    def test_upsert_stores_a_vector_and_no_user_derived_topics(self):
        self._store(["py1"])
        row = self.conn.execute(
            "SELECT topics, course_embedding IS NOT NULL, vector_dims(course_embedding) FROM courses"
        ).fetchone()
        self.assertEqual(row, ([], True, 1536))

    def test_reupsert_keeps_stored_topics_and_embeds_them(self):
        self._store(["py1"])
        self.conn.execute("UPDATE courses SET topics = '{automation}'")
        self._store(["py1"])
        topics = self.conn.execute("SELECT topics FROM courses").fetchone()[0]
        self.assertEqual(topics, ["automation"])
        self.assertEqual(self._titles("automation"), ["Python for Everybody"])

    def test_embedder_failure_rolls_back_the_whole_upsert(self):
        set_embedder(BrokenEmbedder())
        with self.assertRaises(ConnectionError):
            self._store(["py1", "py2"])
        self.assertEqual(self.conn.execute("SELECT count(*) FROM courses").fetchone()[0], 0)

    def test_python_query_returns_python_courses_only(self):
        self._store(list(COURSES))
        titles = self._titles("python")
        self.assertEqual(
            sorted(titles),
            sorted(COURSES[k][0] for k in ["py1", "py2", "py3"]),
        )

    def test_unrelated_topic_returns_none_and_planner_goes_to_web(self):
        self._store(list(COURSES))
        state = {"search_filters": _filters("cooking"), "user_memory": UserMemory()}
        state.update(course_cache_lookup_node(state))
        self.assertEqual(state["cache_candidates"], [])
        self.assertTrue(research_planner_node(state)["research_plan"].search_queries)

    def test_three_topical_hits_satisfy_the_planner_and_skip_web_search(self):
        self._store(list(COURSES))
        state = {"search_filters": _filters("python"), "user_memory": UserMemory()}
        state.update(course_cache_lookup_node(state))
        self.assertEqual(len(state["cache_candidates"]), 3)
        self.assertEqual(research_planner_node(state)["research_plan"].search_queries, [])

    def test_structured_filters_still_apply(self):
        self._store(list(COURSES))
        every = search_course_cache(_filters("python"), UserMemory())
        excluded = every[0]
        self.assertNotIn(
            excluded.title, self._titles("python", UserMemory(completed_course_urls=[excluded.url]))
        )
        self.assertNotIn(
            excluded.title, self._titles("python", UserMemory(rejected_course_urls=[excluded.url]))
        )
        self.assertEqual(self._titles("python", UserMemory(avoided_providers=["coursera"])), [])
        self.assertEqual(self._titles("python", level="advanced"), self._titles("python", level="advanced"))
        self.conn.execute("UPDATE courses SET level = 'beginner', is_free = FALSE")
        self.assertEqual(self._titles("python"), [])
        self.assertEqual(len(self._titles("python", max_price=50)), 3)

    def test_uncertain_and_rejected_rows_are_not_returned(self):
        self._store(["py1"], status="uncertain")
        self._store(["py2"], status="rejected")
        self.assertEqual(self._titles("python"), [])

    def test_ordering_is_stable_and_blends_confidence_and_use(self):
        self._store(["py1", "py2", "py3"])
        first = self._titles("python")
        self.assertEqual(first, self._titles("python"))
        self.conn.execute("UPDATE courses SET use_count = 10 WHERE title = 'Real Python Advanced Tutorials'")
        self.conn.execute("UPDATE courses SET validation_confidence = 1.0 WHERE title = 'Real Python Advanced Tutorials'")
        boosted = self._titles("python")
        self.assertEqual(boosted[0], "Real Python Advanced Tutorials")

    def test_null_embedding_rows_sort_last(self):
        self._store(["py1", "py2", "js1"])
        self.conn.execute("UPDATE courses SET course_embedding = NULL WHERE title LIKE 'The Complete JavaScript%'")
        self.conn.execute("UPDATE courses SET validation_confidence = 1.0, use_count = 10 WHERE title LIKE 'The Complete JavaScript%'")
        titles = self._titles("python")
        self.assertEqual(titles[-1], "The Complete JavaScript Course")
        self.assertEqual(len(titles), 3)

    def test_embedder_failure_propagates(self):
        self._store(["py1"])
        set_embedder(BrokenEmbedder())
        with self.assertRaises(ConnectionError):
            self._titles("python")

    def test_relative_cutoff_drops_courses_far_below_the_best(self):
        set_embedder(FixedEmbedder(relative_cutoff=0.7))
        self._store(["py1", "py2", "js1"])
        self.assertEqual(self._titles("python"), ["Python for Everybody"])
        set_embedder(FixedEmbedder(relative_cutoff=0.5))
        self.assertEqual(len(self._titles("python")), 2)
        set_embedder(FixedEmbedder(relative_cutoff=0.0))
        self.assertEqual(len(self._titles("python")), 2)

    def test_relative_cutoff_is_measured_against_the_filtered_best(self):
        set_embedder(FixedEmbedder(relative_cutoff=0.7))
        self._store(["py1", "py2"])
        self.conn.execute("UPDATE courses SET is_free = FALSE WHERE title = 'Python for Everybody'")
        self.assertEqual(self._titles("python", max_price=0), ["CS50's Introduction to Programming with Python"])

    def test_backfill_all_reembeds_rows_that_already_have_a_vector(self):
        self._store(["py1", "py2"])
        before = self.conn.execute("SELECT course_embedding::text FROM courses ORDER BY id").fetchall()
        set_embedder(FixedEmbedder(relative_cutoff=0.0))
        self.assertEqual(backfill(), 0)
        self.assertEqual(backfill(everything=True), 2)
        after = self.conn.execute("SELECT course_embedding::text FROM courses ORDER BY id").fetchall()
        self.assertNotEqual(before, after)

    def test_backfill_fills_only_null_rows(self):
        self._store(["py1", "py2", "js1"])
        self.conn.execute("UPDATE courses SET course_embedding = NULL WHERE title <> 'Python for Everybody'")
        untouched = self.conn.execute(
            "SELECT course_embedding::text FROM courses WHERE title = 'Python for Everybody'"
        ).fetchone()[0]
        self.conn.execute("UPDATE courses SET topics = '{}'")

        self.assertEqual(backfill(), 2)

        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM courses WHERE course_embedding IS NULL").fetchone()[0], 0
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT course_embedding::text FROM courses WHERE title = 'Python for Everybody'"
            ).fetchone()[0],
            untouched,
        )
        self.assertEqual(backfill(), 0)

    def test_backfill_failure_raises_and_leaves_rows_null(self):
        self._store(["py1"])
        self.conn.execute("UPDATE courses SET course_embedding = NULL")
        set_embedder(BrokenEmbedder())
        with self.assertRaises(ConnectionError):
            backfill()
        self.assertEqual(
            self.conn.execute("SELECT count(*) FROM courses WHERE course_embedding IS NULL").fetchone()[0], 1
        )


if __name__ == "__main__":
    unittest.main()
