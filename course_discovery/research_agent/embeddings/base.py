from __future__ import annotations

from functools import lru_cache
from typing import Protocol, runtime_checkable

EMBEDDING_DIMENSION = 1536


class EmbeddingError(RuntimeError):
    pass


@runtime_checkable
class Embedder(Protocol):
    dimension: int

    def embed(self, texts: list[str]) -> list[list[float]]: ...


_override: Embedder | None = None


@lru_cache(maxsize=1)
def _default_embedder() -> Embedder:
    from course_discovery.research_agent.embeddings.hashing import HashingEmbedder

    return HashingEmbedder()


def set_embedder(embedder: Embedder | None) -> None:
    global _override
    _override = embedder


def get_embedder() -> Embedder:
    return _override or _default_embedder()


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    embedder = get_embedder()
    try:
        vectors = embedder.embed(texts)
    except Exception as exc:  # noqa: BLE001
        raise EmbeddingError(f"Embedding failed: {type(exc).__name__}") from exc
    if embedder.dimension != EMBEDDING_DIMENSION:
        raise EmbeddingError(
            f"Embedder dimension {embedder.dimension} does not match column size {EMBEDDING_DIMENSION}"
        )
    if len(vectors) != len(texts) or any(len(v) != EMBEDDING_DIMENSION for v in vectors):
        raise EmbeddingError("Embedder returned vectors of the wrong count or dimension")
    return vectors


def course_text(title: str, description: str | None, topics: list[str]) -> str:
    return " ".join(part for part in (title, description or "", *topics) if part)


def to_pgvector(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.6g}" for value in vector) + "]"
