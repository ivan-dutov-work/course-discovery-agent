from __future__ import annotations

import math
import unittest

from course_discovery.research_agent.cache.scoring import cosine, topic_score, topic_vector
from course_discovery.research_agent.embeddings import (
    EMBEDDING_DIMENSION,
    EmbeddingError,
    HashingEmbedder,
    course_text,
    embed_texts,
    get_embedder,
    set_embedder,
    to_pgvector,
)


class ConstantEmbedder:
    dimension = EMBEDDING_DIMENSION

    def embed(self, texts):
        return [[1.0] + [0.0] * (EMBEDDING_DIMENSION - 1) for _ in texts]


class WrongDimensionEmbedder:
    dimension = 8

    def embed(self, texts):
        return [[0.0] * 8 for _ in texts]


class RaggedEmbedder:
    dimension = EMBEDDING_DIMENSION

    def embed(self, texts):
        return [[0.0] * 3 for _ in texts]


class BrokenEmbedder:
    dimension = EMBEDDING_DIMENSION

    def embed(self, texts):
        raise ConnectionError("provider down")


class HashingEmbedderTests(unittest.TestCase):
    def setUp(self):
        self.embedder = HashingEmbedder()
        self.addCleanup(set_embedder, None)

    def test_output_is_deterministic(self):
        text = "Python for Everybody: beginner specialization"
        self.assertEqual(self.embedder.embed([text]), HashingEmbedder().embed([text]))

    def test_dimension_matches_the_column(self):
        [vector] = self.embedder.embed(["python"])
        self.assertEqual(len(vector), EMBEDDING_DIMENSION)
        self.assertEqual(self.embedder.dimension, EMBEDDING_DIMENSION)

    def test_vectors_have_unit_norm(self):
        for text in ["python", "machine learning specialization by andrew ng", "sql"]:
            [vector] = self.embedder.embed([text])
            self.assertAlmostEqual(math.sqrt(sum(v * v for v in vector)), 1.0, places=6)

    def test_empty_and_content_free_strings_give_the_zero_vector(self):
        for text in ["", "   ", "the of and", "free beginner certificate"]:
            [vector] = self.embedder.embed([text])
            self.assertFalse(any(vector), text)
            self.assertFalse(any(math.isnan(v) for v in vector))

    def test_same_topic_scores_above_different_topic(self):
        [query, python_doc, js_doc] = self.embedder.embed(
            [
                "python",
                "Python for Everybody. Beginner Python specialization.",
                "The Complete JavaScript Course. Modern JavaScript from scratch.",
            ]
        )
        self.assertGreater(cosine(query, python_doc), 0.2)
        self.assertLess(cosine(query, js_doc), 0.05)

    def test_word_order_matters_through_bigrams(self):
        [query, same, swapped] = self.embedder.embed(
            ["machine learning", "machine learning basics", "learning about machine repair"]
        )
        self.assertGreater(cosine(query, same), cosine(query, swapped))

    def test_facet_words_do_not_change_the_topic_vector(self):
        [plain, faceted] = self.embedder.embed(["python", "Find free Python certificate beginners"])
        self.assertAlmostEqual(cosine(plain, faceted), 1.0, places=6)

    def test_batches_match_single_calls(self):
        texts = ["python", "sql", "react"]
        self.assertEqual(
            self.embedder.embed(texts), [self.embedder.embed([t])[0] for t in texts]
        )


class EmbedderPortTests(unittest.TestCase):
    def setUp(self):
        self.addCleanup(set_embedder, None)

    def test_default_is_the_hashing_embedder(self):
        self.assertIsInstance(get_embedder(), HashingEmbedder)

    def test_swapping_the_embedder_changes_every_caller(self):
        set_embedder(ConstantEmbedder())
        self.assertIsInstance(get_embedder(), ConstantEmbedder)
        self.assertEqual(embed_texts(["anything"])[0][0], 1.0)
        self.assertEqual(topic_vector("python")[0], 1.0)

    def test_reset_restores_the_default(self):
        set_embedder(ConstantEmbedder())
        set_embedder(None)
        self.assertIsInstance(get_embedder(), HashingEmbedder)

    def test_wrong_declared_dimension_raises(self):
        set_embedder(WrongDimensionEmbedder())
        with self.assertRaises(EmbeddingError):
            embed_texts(["python"])

    def test_wrong_vector_length_raises(self):
        set_embedder(RaggedEmbedder())
        with self.assertRaises(EmbeddingError):
            embed_texts(["python"])

    def test_provider_failure_is_wrapped_and_not_swallowed(self):
        set_embedder(BrokenEmbedder())
        with self.assertRaises(EmbeddingError) as ctx:
            embed_texts(["python"])
        self.assertIsInstance(ctx.exception.__cause__, ConnectionError)

    def test_empty_batch_skips_the_embedder(self):
        set_embedder(BrokenEmbedder())
        self.assertEqual(embed_texts([]), [])


class HelperTests(unittest.TestCase):
    def test_course_text_joins_present_parts(self):
        self.assertEqual(course_text("Title", None, ["python", "sql"]), "Title python sql")
        self.assertEqual(course_text("Title", "Desc", []), "Title Desc")

    def test_pgvector_literal_shape(self):
        self.assertEqual(to_pgvector([1.0, 0.0, -0.5]), "[1,0,-0.5]")

    def test_topic_vector_is_none_without_content_words(self):
        self.assertIsNone(topic_vector("free beginner"))

    def test_topic_score_blend(self):
        self.assertAlmostEqual(topic_score(1.0, 1.0, 10), 1.0)
        self.assertAlmostEqual(topic_score(0.0, 0.0, 0), 0.0)
        self.assertGreater(topic_score(0.5, 0.9, 0), topic_score(0.5, 0.5, 0))
        self.assertGreater(topic_score(0.5, 0.5, 5), topic_score(0.5, 0.5, 0))
        self.assertEqual(topic_score(0.5, 0.5, 50), topic_score(0.5, 0.5, 10))
        self.assertGreater(topic_score(0.8, 0.0, 0), topic_score(0.3, 1.0, 10))


if __name__ == "__main__":
    unittest.main()
