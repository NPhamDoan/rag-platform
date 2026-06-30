"""Embedding_Provider interface (R21).

The minimal contract for every Embedding_Provider that self-registers via
`@register_embedding`: generate vectors for a list of texts (used at the document
embedding step — R5.13/R21.1).

Uses `Protocol` (duck-typing) instead of a base class that must be inherited: a provider
only needs a `ten` attribute + an `embed(texts) -> list[list[float]]` method to be
compatible, keeping the registry's self-registration pattern (no inheritance-tree
constraint).

`embed` takes a list of texts (passages/documents) → returns a list of vectors of the
same length, each vector a list[float]. `embed([])` returns `[]`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Embedding_Provider contract: `ten` + `embed(texts)`.

    `runtime_checkable` allows `isinstance(x, EmbeddingProvider)` to check for an `embed`
    method (useful when injecting a fake provider in tests).
    """

    #: Provider name registered in EMBEDDING_REGISTRY (matches `.env`).
    ten: str

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate vectors for a list of texts (passages). `[]` → `[]`."""
        ...
