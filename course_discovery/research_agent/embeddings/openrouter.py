from __future__ import annotations

import os

import httpx

from course_discovery.research_agent.embeddings.base import EMBEDDING_DIMENSION, EmbeddingError

DEFAULT_MODEL = "openai/text-embedding-3-small"
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
BATCH_SIZE = 100


class EmbeddingHTTPError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"embedding request failed with HTTP {status_code}")
        self.status_code = status_code


class OpenRouterEmbedder:
    dimension = EMBEDDING_DIMENSION

    def __init__(
        self,
        api_key: str | None = None,
        *,
        model: str | None = None,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self._api_key:
            raise EmbeddingError("OPENROUTER_API_KEY is required for the openrouter embedder")
        self.model = model or os.getenv("EMBEDDING_MODEL", DEFAULT_MODEL)
        self._url = f"{(base_url or DEFAULT_BASE_URL).rstrip('/')}/embeddings"
        self._client = client or httpx.Client(
            timeout=timeout or float(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "10"))
        )

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            vectors.extend(self._embed_batch(texts[start : start + BATCH_SIZE]))
        return vectors

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        response = self._client.post(
            self._url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"model": self.model, "input": texts, "dimensions": self.dimension},
        )
        if response.status_code != 200:
            raise EmbeddingHTTPError(response.status_code)
        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in data]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding response was malformed") from exc
        if len(vectors) != len(texts) or any(len(v) != self.dimension for v in vectors):
            raise EmbeddingError(
                f"Embedding response has the wrong count or dimension for model {self.model}"
            )
        return vectors
