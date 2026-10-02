from __future__ import annotations

import os
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import psycopg
from pydantic import ValidationError

from course_discovery.domain.models import (
    CourseCandidate,
    MemoryNote,
    MemoryPatch,
    SearchFilters,
    UserMemory,
)
from course_discovery.persistence.checkpointer import open_checkpointer
from course_discovery.privacy import erase_user
from course_discovery.research_agent.memory import repository
from course_discovery.research_agent.memory.defaults import apply_profile_defaults
from course_discovery.research_agent.memory.repository import (
    apply_patch,
    load_user_memory,
    save_user_memory,
)
from course_discovery.research_agent.synthesis import nodes as synthesis
from course_discovery.review.router import router_node


def _course(url: str, provider: str, level: str = "beginner", **kwargs) -> CourseCandidate:
    return CourseCandidate(
        title=url, provider=provider, url=url, level=level, source="tavily", language="en", **kwargs
    )


class PatchTests(unittest.TestCase):
    def test_set_add_remove_and_notes_merge_into_memory(self):
        memory = UserMemory(
            preferred_level="beginner",
            avoided_providers=["udemy"],
            notes=[MemoryNote(text="likes labs", scope="durable")],
        )
        merged = apply_patch(
            memory,
            MemoryPatch(
                set={"preferred_level": "advanced", "preferred_course_length": "short"},
                add={"avoided_providers": ["udemy", "edx"], "career_goals": ["rust"]},
                remove={"avoided_providers": ["udemy"]},
                add_notes=[
                    MemoryNote(text="likes labs", scope="durable"),
                    MemoryNote(text="theory ok", scope="topic:math"),
                ],
            ),
        )
        self.assertEqual(merged.preferred_level, "advanced")
        self.assertEqual(merged.preferred_course_length, "short")
        self.assertEqual(merged.avoided_providers, ["edx"])
        self.assertEqual(merged.career_goals, ["rust"])
        self.assertEqual([n.text for n in merged.notes], ["likes labs", "theory ok"])

    def test_none_clears_a_scalar(self):
        merged = apply_patch(UserMemory(budget_preference="free"), MemoryPatch(set={"budget_preference": None}))
        self.assertIsNone(merged.budget_preference)

    def test_unknown_fields_and_bad_values_are_rejected(self):
        with self.assertRaises(ValidationError):
            MemoryPatch(set={"user_id": "someone-else"})
        with self.assertRaises(ValidationError):
            MemoryPatch(add={"preferred_level": ["x"]})
        with self.assertRaises(ValidationError):
            MemoryPatch(set={"certificate_importance": "maybe"})
        with self.assertRaises(ValidationError):
            MemoryNote(text="x", scope="forever")


class ProfileDefaultsTests(unittest.TestCase):
    def test_stored_budget_applies_when_the_query_is_silent(self):
        filters = apply_profile_defaults(
            SearchFilters(max_price=999.0), "python courses", UserMemory(budget_preference="free")
        )
        self.assertEqual(filters.max_price, 0.0)

    def test_numeric_budget_becomes_a_ceiling(self):
        filters = apply_profile_defaults(SearchFilters(), "python courses", UserMemory(budget_preference="50"))
        self.assertEqual(filters.max_price, 50.0)

    def test_query_that_states_a_price_wins(self):
        filters = apply_profile_defaults(
            SearchFilters(max_price=999.0), "paid python courses", UserMemory(budget_preference="free")
        )
        self.assertEqual(filters.max_price, 999.0)

    def test_certificate_importance_sets_the_default_only_when_unstated(self):
        irrelevant = UserMemory(certificate_importance="irrelevant")
        required = UserMemory(certificate_importance="required")
        self.assertFalse(apply_profile_defaults(SearchFilters(), "python courses", irrelevant).include_certificate)
        self.assertTrue(
            apply_profile_defaults(SearchFilters(include_certificate=False), "python courses", required).include_certificate
        )
        self.assertTrue(
            apply_profile_defaults(SearchFilters(), "python course with certificate", irrelevant).include_certificate
        )

    def test_unparseable_budget_and_empty_profile_change_nothing(self):
        base = SearchFilters(max_price=12.0)
        self.assertEqual(apply_profile_defaults(base, "python", UserMemory(budget_preference="whatever")), base)
        self.assertEqual(apply_profile_defaults(base, "python", UserMemory()), base)


class RankingConsumerTests(unittest.TestCase):
    def setUp(self):
        self.courses = [
            _course("a", "coursera", "beginner"),
            _course("b", "udemy", "intermediate"),
            _course("c", "edx", "advanced", is_free=True),
        ]

    def order(self, memory: UserMemory | None) -> list[str]:
        return [c.url for c in synthesis._rank_courses(self.courses, memory)]

    def test_no_profile_keeps_the_default_order(self):
        self.assertEqual(self.order(None), self.order(UserMemory()))
        self.assertEqual(self.order(None)[0], "c")

    def test_preferred_provider_moves_to_the_front(self):
        self.assertEqual(self.order(UserMemory(preferred_providers=["Udemy"]))[0], "b")

    def test_preferred_level_moves_to_the_front(self):
        self.assertEqual(self.order(UserMemory(preferred_level="beginner"))[0], "a")

    def test_preferred_language_breaks_ties_between_matches(self):
        self.courses[0] = _course("a", "coursera", "beginner")
        self.courses[1] = _course("b", "udemy", "beginner").model_copy(update={"language": "de"})
        memory = UserMemory(preferred_level="beginner", preferred_languages=["de"])
        self.assertEqual(self.order(memory)[0], "b")


class ReaderNotesTests(unittest.TestCase):
    def setUp(self):
        self.memory = UserMemory(
            notes=[
                MemoryNote(text="wants hands-on projects", scope="durable"),
                MemoryNote(text="theory is fine", scope="topic:math"),
                MemoryNote(text="prefers exercises", scope="topic:python"),
            ]
        )

    def test_durable_and_matching_topic_notes_only(self):
        notes = synthesis._reader_notes(self.memory, "Python for data")
        self.assertEqual(notes, ["wants hands-on projects", "prefers exercises"])

    def test_notes_reach_the_synthesis_prompt(self):
        class Recorder:
            def __init__(self):
                self.messages = None

            def invoke(self, messages):
                self.messages = messages

                class Reply:
                    content = "ok"

                return Reply()

        llm = Recorder()
        with patch.object(synthesis, "llm_enabled", return_value=True):
            synthesis._highlight_with_retry(
                llm,
                _course("a", "coursera"),
                None,
                reader_notes=synthesis._reader_notes(self.memory, "python"),
                run_id="r",
                course_idx=1,
            )
        system = llm.messages[0].content
        self.assertIn("wants hands-on projects", system)
        self.assertIn("prefers exercises", system)
        self.assertNotIn("theory is fine", system)


class FeedbackHistoryNodeTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("OPENROUTER_API_KEY", None)

    def test_each_round_is_appended_redacted(self):
        update = router_node(
            {
                "manager_feedback": "rewrite: too basic, mail me at jane.doe@example.com",
                "iteration_count": 0,
                "max_iterations": 3,
                "run_id": "r",
            }
        )
        (entry,) = update["feedback_history"]
        self.assertIn("too basic", entry)
        self.assertNotIn("jane.doe@example.com", entry)

    def test_empty_feedback_adds_nothing(self):
        update = router_node(
            {"manager_feedback": "  ", "iteration_count": 0, "max_iterations": 3, "run_id": "r"}
        )
        self.assertNotIn("feedback_history", update)


@unittest.skipUnless(os.getenv("TEST_DATABASE_URL"), "TEST_DATABASE_URL not set")
class ProfileStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.url = os.environ["TEST_DATABASE_URL"]
        self.user = f"profile-{uuid.uuid4().hex[:8]}"
        env = patch.dict(os.environ, {"DATABASE_URL": self.url})
        env.start()
        self.addCleanup(env.stop)
        self.conn = psycopg.connect(self.url, autocommit=True)
        self.addCleanup(self.conn.close)
        self.addCleanup(lambda: self.conn.execute("DELETE FROM users WHERE id = %s", (self.user,)))

    def test_save_then_load_round_trips_every_learned_field(self):
        save_user_memory(
            self.user,
            MemoryPatch(
                set={
                    "preferred_level": "intermediate",
                    "budget_preference": "free",
                    "certificate_importance": "required",
                    "preferred_course_length": "short",
                },
                add={
                    "preferred_providers": ["coursera"],
                    "avoided_providers": ["udemy"],
                    "rejected_course_urls": ["https://x.test/a"],
                    "career_goals": ["rust"],
                },
                add_notes=[MemoryNote(text="hands-on for python", scope="topic:python", source_run_id="r1")],
            ),
        )
        memory = load_user_memory(self.user)
        self.assertEqual(memory.preferred_level, "intermediate")
        self.assertEqual(memory.budget_preference, "free")
        self.assertEqual(memory.certificate_importance, "required")
        self.assertEqual(memory.preferred_course_length, "short")
        self.assertEqual(memory.preferred_providers, ["coursera"])
        self.assertEqual(memory.avoided_providers, ["udemy"])
        self.assertEqual(memory.rejected_course_urls, ["https://x.test/a"])
        self.assertEqual(memory.career_goals, ["rust"])
        (note,) = memory.notes
        self.assertEqual((note.text, note.scope, note.source_run_id), ("hands-on for python", "topic:python", "r1"))

    def test_second_save_merges_into_the_first(self):
        save_user_memory(self.user, MemoryPatch(add={"avoided_providers": ["udemy"]}))
        save_user_memory(
            self.user, MemoryPatch(add={"avoided_providers": ["edx"]}, set={"preferred_level": "advanced"})
        )
        memory = load_user_memory(self.user)
        self.assertEqual(memory.avoided_providers, ["udemy", "edx"])
        self.assertEqual(memory.preferred_level, "advanced")

    def test_concurrent_saves_do_not_lose_updates(self):
        providers = [f"p{i}" for i in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda p: save_user_memory(self.user, MemoryPatch(add={"avoided_providers": [p]})), providers))
        self.assertEqual(sorted(load_user_memory(self.user).avoided_providers), sorted(providers))

    def test_a_failing_save_raises_and_leaves_nothing_behind(self):
        with patch.object(repository, "apply_patch", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                save_user_memory(self.user, MemoryPatch(add={"avoided_providers": ["udemy"]}))
        self.assertEqual(load_user_memory(self.user), UserMemory())
        self.assertEqual(self.conn.execute("SELECT count(*) FROM users WHERE id = %s", (self.user,)).fetchone()[0], 0)

    def test_free_text_is_redacted_before_it_is_stored(self):
        save_user_memory(
            self.user,
            MemoryPatch(
                add_notes=[MemoryNote(text="ask me at jane.doe@example.com first", scope="durable")],
                set={"learning_style_notes": "call 415-555-0134 after class"},
            ),
        )
        stored = repr(self.conn.execute(
            "SELECT raw_memory_json, learning_style_notes FROM user_preferences WHERE user_id = %s", (self.user,)
        ).fetchone())
        self.assertNotIn("jane.doe@example.com", stored)
        self.assertNotIn("415-555-0134", stored)

    def test_empty_patch_and_missing_user_write_nothing(self):
        self.assertIsNone(save_user_memory(self.user, MemoryPatch()))
        self.assertIsNone(save_user_memory(None, MemoryPatch(add={"avoided_providers": ["x"]})))
        self.assertEqual(load_user_memory(self.user), UserMemory())

    async def test_erasing_a_user_removes_the_saved_profile(self):
        save_user_memory(self.user, MemoryPatch(add={"avoided_providers": ["udemy"]}))
        async with open_checkpointer() as saver:
            report = await erase_user(self.user, saver, execute=True)
        self.assertTrue(report.clean, report.residual)
        self.assertEqual(load_user_memory(self.user), UserMemory())
