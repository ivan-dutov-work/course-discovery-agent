from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from unittest.mock import patch

import psycopg
from pydantic import ValidationError

from course_discovery.domain.models import (
    CourseCandidate,
    MemoryNote,
    MemoryPatch,
    SearchFilters,
    TavilySearchResult,
    UserMemory,
    length_bucket,
)
from course_discovery.memory_curator.tools import propose
from course_discovery.research_agent.cache.repository import search_course_cache, upsert_courses
from course_discovery.research_agent.cache.seed_data import seed_cache
from course_discovery.research_agent.embeddings import EMBEDDING_DIMENSION, EmbeddingError, set_embedder
from course_discovery.research_agent.extraction.nodes import _extract_result
from course_discovery.research_agent.memory.profile_vector import profile_text, profile_vector
from course_discovery.research_agent.memory.repository import save_user_memory
from course_discovery.research_agent.search.mock_catalog import CATALOG
from course_discovery.research_agent.search.tavily_client import TavilyClient
from course_discovery.research_agent.synthesis import nodes as synthesis


def _course(url: str, **kwargs) -> CourseCandidate:
    kwargs.setdefault("provider", "p")
    return CourseCandidate(title=url, url=url, source="tavily", **kwargs)


def _order(courses, memory=None) -> list[str]:
    return [c.url for c in synthesis._rank_courses(courses, memory)]


class ProfileVectorTests(unittest.TestCase):
    def test_text_joins_the_fields_that_describe_taste(self):
        memory = UserMemory(
            preferred_level="beginner",
            preferred_providers=["coursera"],
            career_goals=["data engineer"],
            learning_style_notes="hands-on",
            notes=[MemoryNote(text="likes projects", scope="topic:python")],
            avoided_providers=["udemy"],
            budget_preference="free",
        )
        text = profile_text(memory)
        for part in ("beginner", "coursera", "data engineer", "hands-on", "likes projects"):
            self.assertIn(part, text)
        self.assertNotIn("udemy", text)
        self.assertNotIn("free", text)

    def test_empty_profile_has_no_vector(self):
        self.assertEqual(profile_text(UserMemory()), "")
        self.assertIsNone(profile_vector(UserMemory()))

    def test_vector_has_the_column_dimension(self):
        vector = profile_vector(UserMemory(career_goals=["python data analysis"]))
        self.assertEqual(len(vector), EMBEDDING_DIMENSION)


class SeedCacheProfileTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_embedder, None)
        self.filters = SearchFilters(topic="")

    def test_no_profile_leaves_similarity_unset(self):
        self.assertTrue(all(c.profile_similarity is None for c in seed_cache(self.filters, UserMemory(), limit=9)))

    def test_profile_sets_a_scalar_similarity_closer_for_matching_goals(self):
        memory = UserMemory(career_goals=["harvard programming python"])
        scored = {c.title: c.profile_similarity for c in seed_cache(self.filters, memory, limit=9)}
        self.assertTrue(all(isinstance(v, float) for v in scored.values()))
        self.assertGreater(scored["CS50's Introduction to Programming with Python"], scored["Python for Everybody"])

    def test_embedder_failure_propagates(self):
        class Broken:
            dimension = EMBEDDING_DIMENSION

            def embed(self, texts):
                raise ValueError("bad")

        set_embedder(Broken())
        with self.assertRaises(EmbeddingError):
            seed_cache(self.filters, UserMemory(career_goals=["x"]), limit=9)


class ProfileTieBreakTests(unittest.TestCase):
    def test_closer_profile_wins_between_otherwise_equal_courses(self):
        far = _course("far", is_free=True, rating=4.9, profile_similarity=0.1)
        near = _course("near", is_free=True, rating=4.0, profile_similarity=0.6)
        self.assertEqual(_order([far, near]), ["near", "far"])

    def test_explicit_preferences_outrank_the_tie_break(self):
        liked = _course("liked", provider="coursera", profile_similarity=0.0)
        close = _course("close", profile_similarity=0.9)
        memory = UserMemory(preferred_providers=["coursera"])
        self.assertEqual(_order([close, liked], memory), ["liked", "close"])

    def test_price_outranks_the_tie_break(self):
        free = _course("free", is_free=True, profile_similarity=0.0)
        paid = _course("paid", is_free=False, profile_similarity=0.9)
        self.assertEqual(_order([paid, free]), ["free", "paid"])

    def test_web_candidates_without_similarity_fall_through_to_rating(self):
        a = _course("a", rating=4.0)
        b = _course("b", rating=4.8)
        self.assertEqual(_order([a, b]), ["b", "a"])

    def test_small_differences_do_not_override_rating(self):
        a = _course("a", rating=4.0, profile_similarity=0.31)
        b = _course("b", rating=4.8, profile_similarity=0.34)
        self.assertEqual(_order([a, b]), ["b", "a"])


class LengthBucketTests(unittest.TestCase):
    def test_boundaries(self):
        self.assertIsNone(length_bucket(None))
        self.assertEqual(length_bucket(10), "short")
        self.assertEqual(length_bucket(10.5), "medium")
        self.assertEqual(length_bucket(40), "medium")
        self.assertEqual(length_bucket(41), "long")

    def test_patch_accepts_the_vocabulary_and_clears(self):
        for value in ("short", "medium", "long", None):
            MemoryPatch(set={"preferred_course_length": value})

    def test_patch_rejects_free_text(self):
        with self.assertRaises(ValidationError):
            MemoryPatch(set={"preferred_course_length": "2h/week"})

    def test_curator_may_write_it_and_gets_an_error_for_free_text(self):
        good, message = propose(
            {"scope": "durable", "reason": "x", "patch": {"set": {"preferred_course_length": "short"}}},
            profile_read=True,
        )
        self.assertEqual(good["set"], {"preferred_course_length": "short"})
        bad, message = propose(
            {"scope": "durable", "reason": "x", "patch": {"set": {"preferred_course_length": "2h/week"}}},
            profile_read=True,
        )
        self.assertIsNone(bad)
        self.assertTrue(message.startswith("error:"))


def _result(snippet: str, **metadata) -> TavilySearchResult:
    return TavilySearchResult(
        query="q", title="Course", url="https://x.test/c", snippet=snippet, raw_metadata=metadata
    )


class DurationExtractionTests(unittest.TestCase):
    def test_structured_metadata_wins(self):
        self.assertEqual(_extract_result(_result("About 99 hours.", duration_hours=12)).duration_hours, 12.0)

    def test_hours_are_read_from_the_snippet(self):
        self.assertEqual(_extract_result(_result("Self-paced, 8 hours of video.")).duration_hours, 8.0)
        self.assertEqual(_extract_result(_result("Roughly 6.5 hrs total.")).duration_hours, 6.5)

    def test_weekly_effort_is_not_a_duration(self):
        self.assertIsNone(_extract_result(_result("Plan for 5 hours per week.")).duration_hours)
        self.assertIsNone(_extract_result(_result("Plan for 5 hours a week.")).duration_hours)

    def test_missing_or_invalid_duration_is_none(self):
        self.assertIsNone(_extract_result(_result("Beginner course.")).duration_hours)
        self.assertIsNone(_extract_result(_result("Beginner course.", duration_hours=0)).duration_hours)

    def test_every_mock_listing_reaches_the_candidate(self):
        results = asyncio.run(TavilyClient().search("python javascript sql react", max_results=20))
        by_url = {listing.url: listing.duration_hours for listing in CATALOG}
        self.assertTrue(results)
        for result in results:
            self.assertEqual(_extract_result(result).duration_hours, float(by_url[result.url]))


class LengthRankingTests(unittest.TestCase):
    def setUp(self):
        self.long = _course("long", is_free=True, rating=4.9, duration_hours=80)
        self.short = _course("short", is_free=True, rating=4.0, duration_hours=6)
        self.unknown = _course("unknown", is_free=True, rating=4.5)

    def test_matching_length_moves_to_the_front(self):
        memory = UserMemory(preferred_course_length="short")
        self.assertEqual(_order([self.long, self.unknown, self.short], memory)[0], "short")

    def test_no_preference_keeps_the_default_order(self):
        self.assertEqual(_order([self.short, self.long, self.unknown]), ["long", "unknown", "short"])

    def test_unknown_duration_never_matches(self):
        memory = UserMemory(preferred_course_length="long")
        self.assertEqual(_order([self.unknown, self.long], memory)[0], "long")
        self.assertEqual(_order([self.short, self.unknown], memory), ["unknown", "short"])


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class PostgresProfileAndDurationTests(unittest.TestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.user = f"pv-{uuid.uuid4().hex[:8]}"
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        self.conn = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.conn.close)
        self.addCleanup(lambda: self.conn.execute("DELETE FROM users WHERE id = %s", (self.user,)))
        self.course_url = f"https://pv.test/{uuid.uuid4().hex}"
        self.addCleanup(lambda: self.conn.execute("DELETE FROM courses WHERE canonical_url = %s", (self.course_url,)))

    def _embedding_is_null(self) -> bool:
        return self.conn.execute(
            "SELECT profile_embedding IS NULL FROM user_preferences WHERE user_id = %s", (self.user,)
        ).fetchone()[0]

    def test_save_writes_the_vector_and_clearing_the_text_nulls_it(self):
        save_user_memory(self.user, MemoryPatch(add={"career_goals": ["python data analysis"]}))
        self.assertFalse(self._embedding_is_null())
        save_user_memory(self.user, MemoryPatch(remove={"career_goals": ["python data analysis"]}))
        self.assertTrue(self._embedding_is_null())

    def test_failed_embedding_rolls_the_save_back(self):
        class Broken:
            dimension = EMBEDDING_DIMENSION

            def embed(self, texts):
                raise ValueError("bad")

        set_embedder(Broken())
        self.addCleanup(set_embedder, None)
        with self.assertRaises(EmbeddingError):
            save_user_memory(self.user, MemoryPatch(add={"career_goals": ["python"]}))
        self.assertIsNone(self.conn.execute("SELECT 1 FROM user_preferences WHERE user_id = %s", (self.user,)).fetchone())

    def test_cache_search_returns_duration_and_profile_similarity(self):
        course = CourseCandidate(
            title="Python data analysis", provider="p", url=self.course_url, description="python data analysis",
            is_free=True, has_certificate=True, level="beginner", language="en", source="tavily",
            confidence=0.9, duration_hours=12.0,
        )
        from course_discovery.domain.models import CandidateValidation

        upsert_courses([course], [CandidateValidation(url=self.course_url, status="valid")])
        save_user_memory(self.user, MemoryPatch(add={"career_goals": ["python data analysis"]}))
        filters = SearchFilters(topic="python data analysis")
        found = {c.url: c for c in search_course_cache(filters, UserMemory(), limit=50, user_id=self.user)}
        self.assertEqual(found[self.course_url].duration_hours, 12.0)
        self.assertGreater(found[self.course_url].profile_similarity, 0.5)
        anonymous = {c.url: c for c in search_course_cache(filters, UserMemory(), limit=50)}
        self.assertIsNone(anonymous[self.course_url].profile_similarity)
