from __future__ import annotations

import json
import os
import unittest
from unittest import mock

import httpx

from course_discovery.research_agent.embeddings import (
    EMBEDDING_DIMENSION,
    EmbeddingError,
    HashingEmbedder,
    OpenRouterEmbedder,
    embed_texts,
    get_embedder,
    set_embedder,
)
from course_discovery.research_agent.embeddings import base
from course_discovery.resilience import is_transient


def _vector(seed: float, size: int = EMBEDDING_DIMENSION) -> list[float]:
    return [seed] + [0.0] * (size - 1)


def _embedder(handler, **kwargs) -> OpenRouterEmbedder:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenRouterEmbedder("test-key", client=client, **kwargs)


class OpenRouterEmbedderTests(unittest.TestCase):
    def tearDown(self):
        set_embedder(None)

    def test_success_returns_vectors_in_input_order(self):
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["body"] = json.loads(request.content)
            seen["auth"] = request.headers["authorization"]
            data = [
                {"index": 1, "embedding": _vector(2.0)},
                {"index": 0, "embedding": _vector(1.0)},
            ]
            return httpx.Response(200, json={"data": data})

        vectors = _embedder(handler).embed(["a", "b"])
        self.assertEqual([v[0] for v in vectors], [1.0, 2.0])
        self.assertEqual(seen["auth"], "Bearer test-key")
        self.assertEqual(seen["body"]["input"], ["a", "b"])
        self.assertEqual(seen["body"]["dimensions"], EMBEDDING_DIMENSION)

    def test_large_input_is_split_into_batches(self):
        sizes = []

        def handler(request: httpx.Request) -> httpx.Response:
            texts = json.loads(request.content)["input"]
            sizes.append(len(texts))
            data = [{"index": i, "embedding": _vector(1.0)} for i in range(len(texts))]
            return httpx.Response(200, json={"data": data})

        self.assertEqual(len(_embedder(handler).embed(["x"] * 250)), 250)
        self.assertEqual(sizes, [100, 100, 50])

    def test_wrong_dimension_raises_embedding_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector(1.0, 8)}]})

        with self.assertRaises(EmbeddingError):
            _embedder(handler).embed(["a"])

    def test_wrong_count_raises_embedding_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": _vector(1.0)}]})

        with self.assertRaises(EmbeddingError):
            _embedder(handler).embed(["a", "b"])

    def test_malformed_body_raises_embedding_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "nope"})

        with self.assertRaises(EmbeddingError):
            _embedder(handler).embed(["a"])

    def test_transport_error_propagates_as_transient(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("down")

        set_embedder(_embedder(handler))
        with self.assertRaises(httpx.ConnectError) as ctx:
            embed_texts(["a"])
        self.assertTrue(is_transient(ctx.exception))

    def test_rate_limit_and_server_errors_are_transient(self):
        for status in (429, 503):
            with self.subTest(status=status):
                set_embedder(_embedder(lambda request, s=status: httpx.Response(s)))
                with self.assertRaises(Exception) as ctx:
                    embed_texts(["a"])
                self.assertTrue(is_transient(ctx.exception))

    def test_client_error_is_not_transient(self):
        set_embedder(_embedder(lambda request: httpx.Response(401)))
        with self.assertRaises(Exception) as ctx:
            embed_texts(["a"])
        self.assertFalse(is_transient(ctx.exception))

    def test_missing_key_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(EmbeddingError):
                OpenRouterEmbedder()


class EmbedderSwitchTests(unittest.TestCase):
    def setUp(self):
        base._default_embedder.cache_clear()
        self.addCleanup(base._default_embedder.cache_clear)
        self.addCleanup(set_embedder, None)

    def test_default_is_hashing(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertIsInstance(get_embedder(), HashingEmbedder)

    def test_env_selects_openrouter(self):
        with mock.patch.dict(os.environ, {"EMBEDDER": "openrouter", "OPENROUTER_API_KEY": "k"}):
            self.assertIsInstance(get_embedder(), OpenRouterEmbedder)

    def test_unknown_value_fails_closed(self):
        with mock.patch.dict(os.environ, {"EMBEDDER": "bogus"}):
            with self.assertRaises(EmbeddingError):
                get_embedder()


@unittest.skipUnless(os.getenv("OPENROUTER_API_KEY"), "OPENROUTER_API_KEY not set")
class LiveOpenRouterEmbedderTests(unittest.TestCase):
    def test_related_text_is_closer_than_unrelated(self):
        embedder = OpenRouterEmbedder()
        query, related, unrelated = embedder.embed(
            ["learn python programming", "Python for Everybody specialization", "sourdough baking guide"]
        )
        dot = lambda a, b: sum(x * y for x, y in zip(a, b))  # noqa: E731
        self.assertGreater(dot(query, related), dot(query, unrelated))
