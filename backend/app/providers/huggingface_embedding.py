"""HuggingFace e5-large Embedding_Provider (intfloat/multilingual-e5-large).

Self-registers via `@register_embedding("huggingface")`. Implements `embed()` per the
`EmbeddingProvider` interface (R21.1).

IMPORTANT — import-safe + LAZY model loading:
- This file does NOT load the heavy library (`sentence-transformers`) or the ~2GB model
  at import time. Doing so would make `discover_providers()` / the startup fail-fast
  check fail on environments where it is not installed. The model is loaded only on the
  first call to `embed()`.
- If the library/model is unavailable when embedding → raise `InitializationError` with
  a clear reason (so the upper layer can handle it: an embedding step failure → keep the
  state unchanged, R5.13).

e5 convention (mirrors the legacy law provider): prepend `"passage: "` for document
texts and `"query: "` for queries. `embed()` is for documents so it prepends
`"passage: "`; `embedQuery()` serves retrieval (the query layer, a later task).
"""

from __future__ import annotations

import logging

from app.errors import InitializationError
from app.providers.registry import register_embedding

logger = logging.getLogger(__name__)

#: Default embedding model (1024 dimensions) — inherited from Vietnam Law RAG.
_MODEL_NAME = "intfloat/multilingual-e5-large"
_QUERY_PREFIX = "query: "
_PASSAGE_PREFIX = "passage: "

_THIEU_THU_VIEN = (
    "Khong nap duoc thu vien embedding (sentence-transformers chua duoc cai dat)."
)
_KHONG_NAP_DUOC_MODEL = "Khong nap duoc model embedding '{ten}'."


@register_embedding("huggingface")
class HuggingFaceEmbeddingProvider:
    """e5-large Embedding_Provider. Loads the model lazily on the first embed() call."""

    ten = "huggingface"
    #: Vector dimension of intfloat/multilingual-e5-large.
    dimension = 1024

    def __init__(self, modelName: str = _MODEL_NAME) -> None:
        self._modelName = modelName
        self._model = None  # loaded lazily

    # --- Lazy model loading ------------------------------------------------
    def _loadModel(self):
        """Load `SentenceTransformer` once; missing library/model → InitializationError."""
        if self._model is not None:
            return self._model
        try:
            from sentence_transformers import SentenceTransformer
        except Exception as exc:  # noqa: BLE001 — missing heavy library
            logger.error("Khong nap duoc sentence-transformers: %s", exc)
            raise InitializationError(_THIEU_THU_VIEN) from exc
        try:
            logger.info("Nap model embedding '%s' (lazy)...", self._modelName)
            self._model = SentenceTransformer(self._modelName)
        except Exception as exc:  # noqa: BLE001 — model failed to load
            logger.error("Khong nap duoc model embedding '%s': %s", self._modelName, exc)
            raise InitializationError(
                _KHONG_NAP_DUOC_MODEL.format(ten=self._modelName)
            ) from exc
        return self._model

    # --- Embed -------------------------------------------------------------
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Generate vectors for a list of document texts (prefix `passage: `)."""
        if not texts:
            return []
        return self._encode([_PASSAGE_PREFIX + t for t in texts])

    def embedQuery(self, text: str) -> list[float]:
        """Generate a vector for a single query (prefix `query: `) — used in the query layer."""
        return self._encode([_QUERY_PREFIX + text])[0]

    def _encode(self, inputs: list[str]) -> list[list[float]]:
        """Call model encode + L2 normalize; return list[list[float]]."""
        model = self._loadModel()
        vectors = model.encode(inputs, normalize_embeddings=True)
        return [[float(x) for x in v] for v in vectors]
