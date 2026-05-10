"""Embedding provider wrapper.

The default provider calls the Voyage AI API via the anthropic SDK's
voyage client. In tests, swap out ``_provider`` with a stub that returns
fixed-length vectors without making network calls.
"""

from __future__ import annotations

import abc
import logging

from fundprint.config import settings

logger = logging.getLogger(__name__)


class EmbeddingProvider(abc.ABC):
    """Abstract interface for embedding providers."""

    model: str  # must be set on every concrete subclass

    @abc.abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one float vector per input text."""


class VoyageProvider(EmbeddingProvider):
    """Voyage AI embedding via the voyage Python client.

    Raises ImportError at construction time if ``voyageai`` is not installed,
    rather than at call time - fail fast so tests can monkeypatch before import.
    """

    # The model is stored alongside every embedding row so that cross-model
    # cosine comparisons can be blocked at query time.
    model: str

    def __init__(self, model: str | None = None) -> None:
        try:
            import voyageai  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "voyageai package is required for the default embedding provider; "
                "install it with: pip install voyageai"
            ) from exc
        self.model = model or settings.embedding_model
        self._client = voyageai.Client()

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Call the Voyage API and return embeddings in the same order as input."""
        result = self._client.embed(texts, model=self.model)
        return result.embeddings


class StubProvider(EmbeddingProvider):
    """Deterministic stub for use in tests.

    Returns a zero vector of the specified dimension. Never makes network calls.
    """

    model = "stub-0"

    def __init__(self, dim: int = 1024) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]


# Module-level provider instance. Monkeypatched in tests.
_provider: EmbeddingProvider | None = None


def _get_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        _provider = VoyageProvider()
    return _provider


def embed(texts: list[str]) -> tuple[list[list[float]], str]:
    """Embed a list of texts; returns (vectors, model_name).

    The model name must be persisted alongside every embedding row
    so resolution can block cross-model cosine comparisons.
    """
    provider = _get_provider()
    vectors = provider.embed(texts)
    return vectors, provider.model
