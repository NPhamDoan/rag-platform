"""The "semantic" chunking strategy: group paragraphs by target size (R15, R17).

A simple, deterministic semantic chunker that does NOT call embeddings: it splits text
into paragraphs (separated by blank lines), then groups consecutive paragraphs until
close to the target size. Keeps paragraph boundaries so each chunk is a meaningful unit.
A single paragraph that is too long → split further by size.
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
    split_spans,
)
from app.chunking.registry import register_chunker

logger = logging.getLogger(__name__)

# Paragraph boundary: one or more blank lines.
_PARAGRAPH_SPLIT = re.compile(r"\n[ \t]*\n")


@register_chunker("semantic")
class SemanticChunker(ChunkerBase):
    """Group consecutive paragraphs to the target size (deterministic, no embedding)."""

    ten = "semantic"

    def chunk(
        self,
        text: str,
        thamSo: "ChunkParams | Mapping[str, Any] | None" = None,
        rules: list | None = None,
    ) -> list[ChunkData]:
        if not text or not text.strip():
            return []
        params = ChunkParams.from_any(thamSo)

        # Get the paragraphs with their absolute (start, end) positions in the original text.
        paragraphs = self._split_paragraphs(text)

        chunks: list[ChunkData] = []
        nhom_start: int | None = None
        nhom_end: int | None = None
        for p_start, p_end in paragraphs:
            do_dai_doan = p_end - p_start
            if do_dai_doan > params.kichThuocMucTieu:
                # Flush the group being accumulated, then split the long paragraph by size.
                self._flush(text, nhom_start, nhom_end, chunks)
                nhom_start = nhom_end = None
                for sp_start, sp_end in split_spans(text[p_start:p_end], params):
                    chunk = make_chunk(
                        text, p_start + sp_start, p_start + sp_end, len(chunks),
                        {"chienLuoc": self.ten},
                    )
                    if chunk is not None:
                        chunks.append(chunk)
                continue
            if nhom_start is None:
                nhom_start, nhom_end = p_start, p_end
            elif (p_end - nhom_start) <= params.kichThuocMucTieu:
                nhom_end = p_end
            else:
                self._flush(text, nhom_start, nhom_end, chunks)
                nhom_start, nhom_end = p_start, p_end
        self._flush(text, nhom_start, nhom_end, chunks)
        logger.debug("semantic: %d doan -> %d chunk", len(paragraphs), len(chunks))
        return chunks

    @staticmethod
    def _split_paragraphs(text: str) -> list[tuple[int, int]]:
        """Return the non-empty paragraph spans (start, end) over the original text."""
        spans: list[tuple[int, int]] = []
        pos = 0
        for m in _PARAGRAPH_SPLIT.finditer(text):
            if text[pos:m.start()].strip():
                spans.append((pos, m.start()))
            pos = m.end()
        if text[pos:].strip():
            spans.append((pos, len(text)))
        return spans

    def _flush(self, text, start, end, chunks: list[ChunkData]) -> None:
        """Build a chunk from the group being accumulated (if any) and append it to `chunks`."""
        if start is None or end is None:
            return
        chunk = make_chunk(text, start, end, len(chunks), {"chienLuoc": self.ten})
        if chunk is not None:
            chunks.append(chunk)
