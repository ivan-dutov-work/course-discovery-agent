from __future__ import annotations

import unittest

from course_discovery.research_agent.cache.scoring import topic_vector
from course_discovery.research_agent.embeddings import course_text, set_embedder
from course_discovery.research_agent.embeddings.facets import strip_facets


class RecordingEmbedder:
    dimension = 1536

    def __init__(self) -> None:
        self.seen: list[str] = []

    def embed(self, texts):
        self.seen.extend(texts)
        return [[1.0] + [0.0] * 1535 for _ in texts]


class StripFacetsTests(unittest.TestCase):
    def test_removes_level_price_and_certificate_words_with_plurals(self):
        self.assertEqual(strip_facets("Find free Python certificate beginners"), "Find Python")

    def test_leaves_words_that_only_start_with_a_facet_word(self):
        self.assertEqual(strip_facets("freeCodeCamp JavaScript"), "freeCodeCamp JavaScript")

    def test_is_case_insensitive(self):
        self.assertEqual(strip_facets("Google Cybersecurity Professional CERTIFICATE"), "Google Cybersecurity Professional")

    def test_facet_only_text_becomes_empty(self):
        self.assertEqual(strip_facets("free beginner"), "")


class EmbeddedTextTests(unittest.TestCase):
    def setUp(self):
        self.embedder = RecordingEmbedder()
        set_embedder(self.embedder)
        self.addCleanup(set_embedder, None)

    def test_topic_is_embedded_without_facet_words(self):
        topic_vector("Find free Python certificate beginners")
        self.assertEqual(self.embedder.seen, ["Find Python"])

    def test_facet_only_topic_is_never_embedded(self):
        self.assertIsNone(topic_vector("free beginner"))
        self.assertIsNone(topic_vector("   "))
        self.assertEqual(self.embedder.seen, [])

    def test_course_text_drops_facet_words_from_title_and_description(self):
        self.assertEqual(
            course_text("Google Cybersecurity Professional Certificate", "Free to audit, beginner level.", ["python"]),
            "Google Cybersecurity Professional to audit, level. python",
        )


if __name__ == "__main__":
    unittest.main()
