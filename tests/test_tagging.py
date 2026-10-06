from __future__ import annotations

import unittest

from course_discovery.research_agent.search.mock_catalog import CATALOG
from course_discovery.research_agent.tagging.tagger import (
    CourseForTagging,
    CourseTagging,
    ModuleTags,
    build_tag_messages,
    clean_tagging,
)
from course_discovery.research_agent.tagging.vocabulary import TOPIC_VOCABULARY


def _course(listing) -> CourseForTagging:
    return CourseForTagging(listing.title, listing.snippet, tuple(listing.modules))


class PromptPrefixTests(unittest.TestCase):
    def test_system_message_is_identical_across_courses(self) -> None:
        first, second = (build_tag_messages(_course(listing)) for listing in CATALOG[:2])

        self.assertEqual(first[0].content, second[0].content)
        self.assertNotEqual(first[1].content, second[1].content)

    def test_system_message_carries_no_course_data(self) -> None:
        system = build_tag_messages(_course(CATALOG[0]))[0].content

        for listing in CATALOG:
            self.assertNotIn(listing.url, system)
            self.assertNotIn(listing.snippet, system)

    def test_vocabulary_is_sorted_and_unique(self) -> None:
        self.assertEqual(list(TOPIC_VOCABULARY), sorted(set(TOPIC_VOCABULARY)))

    def test_every_catalog_course_has_modules(self) -> None:
        self.assertTrue(all(listing.modules for listing in CATALOG))


class CleanTaggingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.course = CourseForTagging("C", None, ("A", "B", "C"))

    def test_unknown_tags_are_dropped_and_known_kept(self) -> None:
        raw = CourseTagging(modules=[ModuleTags(module="A", tags=["python", "made-up"])])

        cleaned = clean_tagging(self.course, raw)

        self.assertEqual(cleaned[0].tags, ["python"])

    def test_missing_modules_come_back_empty_in_course_order(self) -> None:
        raw = CourseTagging(modules=[ModuleTags(module="C", tags=["sql"])])

        cleaned = clean_tagging(self.course, raw)

        self.assertEqual([m.module for m in cleaned], ["A", "B", "C"])
        self.assertEqual([m.tags for m in cleaned], [[], [], ["sql"]])

    def test_tags_are_deduplicated_and_capped_at_three(self) -> None:
        raw = CourseTagging(
            modules=[
                ModuleTags(module="A", tags=["sql", "sql", "python", "git", "docker", "linux"])
            ]
        )

        cleaned = clean_tagging(self.course, raw)

        self.assertEqual(cleaned[0].tags, ["sql", "python", "git"])


if __name__ == "__main__":
    unittest.main()
