"""The "recursive" chunking strategy: recursive splitting by character boundaries (R15, R17).

Splits text along natural boundaries (paragraph → line → sentence → word) into pieces
of ~kichThuocMucTieu characters, with overlap. This is the general-purpose,
domain-agnostic strategy, used as the default fallback of AutoSelector (task 7.2).
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from app.chunking.base import ChunkData, ChunkerBase, ChunkParams, split_by_size
from app.chunking.registry import register_chunker

logger = logging.getLogger(__name__)


@register_chunker("recursive")
class RecursiveChunker(ChunkerBase):
    """Recursive splitting by character (paragraph/sentence) to the target size + overlap."""

    ten = "recursive"

    def chunk(
        self,
        text: str,
        thamSo: "ChunkParams | Mapping[str, Any] | None" = None,
        rules: list | None = None,
    ) -> list[ChunkData]:
        if not text or not text.strip():
            return []
        params = ChunkParams.from_any(thamSo)
        chunks = split_by_size(text, params, {"chienLuoc": self.ten})
        logger.debug("recursive: %d ky tu -> %d chunk", len(text), len(chunks))
        return chunks
