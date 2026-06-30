"""The "page" chunking strategy: split along page boundaries (R15, R17).

Splits the text on the form-feed page marker `\\f` (PyMuPDF inserts it between pages).
Each page is a chunk; an over-long page is split further by size. Text without
pagination (no `\\f`) → falls back to size-based splitting (still guarantees >= 1 chunk).
"""

from __future__ import annotations

import logging
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

_PAGE_MARKER = "\f"


@register_chunker("page")
class PageChunker(ChunkerBase):
    """Split on the page marker `\\f`; an over-long page is split further by size."""

    ten = "page"

    def chunk(
        self,
        text: str,
        thamSo: "ChunkParams | Mapping[str, Any] | None" = None,
        rules: list | None = None,
    ) -> list[ChunkData]:
        if not text or not text.strip():
            return []
        params = ChunkParams.from_any(thamSo)

        if _PAGE_MARKER not in text:
            logger.debug("page: khong co dau phan trang, fallback size-split")
            return split_by_size(text, params, {"chienLuoc": self.ten})

        chunks: list[ChunkData] = []
        pos = 0
        soTrang = 0
        n = len(text)
        while pos <= n:
            marker = text.find(_PAGE_MARKER, pos)
            page_end = marker if marker != -1 else n
            page_start = pos
            soTrang += 1
            page_text = text[page_start:page_end]
            if page_text.strip():
                if len(page_text) <= params.kichThuocMucTieu:
                    chunk = make_chunk(
                        text, page_start, page_end, len(chunks),
                        {"chienLuoc": self.ten, "trang": soTrang},
                    )
                    if chunk is not None:
                        chunks.append(chunk)
                else:
                    for sp_start, sp_end in split_spans(page_text, params):
                        chunk = make_chunk(
                            text, page_start + sp_start, page_start + sp_end, len(chunks),
                            {"chienLuoc": self.ten, "trang": soTrang},
                        )
                        if chunk is not None:
                            chunks.append(chunk)
            if marker == -1:
                break
            pos = marker + len(_PAGE_MARKER)
        logger.debug("page: %d trang -> %d chunk", soTrang, len(chunks))
        return chunks
