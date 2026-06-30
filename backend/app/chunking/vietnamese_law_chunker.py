"""The "vietnamese-law" chunking strategy: split along Article boundaries (R15, R17).

Inherits the legacy law chunker: splits text at "Dieu N." / "Dieu N:" boundaries (1
Article = 1 chunk). Requires a `.`/`:` after the number to avoid wrongly cutting at
references (e.g. "theo Dieu 5 cua..."). An over-long Article → split further by size +
overlap. Text without Articles (circulars/appendices) → falls back to size-based
splitting (still guarantees >= 1 chunk).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Mapping

from app.chunking.base import (
    ChunkData,
    ChunkerBase,
    ChunkParams,
    make_chunk,
    split_by_size,
    split_spans,
)
from app.chunking.registry import register_chunker

logger = logging.getLogger(__name__)

# Inherits the legacy law chunker's DIEU_BOUNDARY_PATTERN: line start, "Dieu" + number + ./: .
DIEU_BOUNDARY_PATTERN = re.compile(r"^[ \t]*Điều\s+(\d+)\s*[.:]", re.MULTILINE)


@register_chunker("vietnamese-law")
class VietnameseLawChunker(ChunkerBase):
    """Split along Article boundaries; an over-long Article is split further by size."""

    ten = "vietnamese-law"

    def chunk(
        self,
        text: str,
        thamSo: "ChunkParams | Mapping[str, Any] | None" = None,
        rules: list | None = None,
    ) -> list[ChunkData]:
        if not text or not text.strip():
            return []
        params = ChunkParams.from_any(thamSo)

        matches = list(DIEU_BOUNDARY_PATTERN.finditer(text))
        if not matches:
            # Circulars/appendices without Articles → fall back to size-based splitting.
            logger.debug("vietnamese-law: khong co Dieu, fallback size-split")
            return split_by_size(text, params, {"chienLuoc": self.ten})

        chunks: list[ChunkData] = []
        # The preamble before the first Article (e.g. the document title) is also kept as a chunk.
        if matches[0].start() > 0:
            self._emit(text, 0, matches[0].start(), None, params, chunks)

        for i, m in enumerate(matches):
            sec_start = m.start()
            sec_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            soDieu = int(m.group(1))
            self._emit(text, sec_start, sec_end, soDieu, params, chunks)

        logger.debug("vietnamese-law: %d Dieu -> %d chunk", len(matches), len(chunks))
        return chunks

    def _emit(self, text, start, end, soDieu, params, chunks: list[ChunkData]) -> None:
        """Build a chunk for one Article; if it is too long, split further by size."""
        section = text[start:end]
        if not section.strip():
            return
        meta = {"chienLuoc": self.ten}
        if soDieu is not None:
            meta["dieu"] = soDieu
        if len(section) <= params.kichThuocMucTieu:
            chunk = make_chunk(text, start, end, len(chunks), dict(meta))
            if chunk is not None:
                chunks.append(chunk)
            return
        for sp_start, sp_end in split_spans(section, params):
            chunk = make_chunk(
                text, start + sp_start, start + sp_end, len(chunks), dict(meta)
            )
            if chunk is not None:
                chunks.append(chunk)
