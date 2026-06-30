"""The "structure-aware" chunking strategy: split by markdown heading structure (R15, R17).

Splits text along markdown heading lines (`#`, `##`, ...). Each section runs from one
heading to the next; an over-long section is split further by size. If there are no
headings → falls back to size-based splitting (still guarantees >= 1 chunk).
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

# Markdown heading line: 1-6 '#' chars + whitespace + title.
_HEADING_PATTERN = re.compile(r"^[ \t]*#{1,6}[ \t]+\S.*$", re.MULTILINE)


@register_chunker("structure-aware")
class StructureChunker(ChunkerBase):
    """Split by markdown headings; an over-long section is split further by size."""

    ten = "structure-aware"

    def chunk(
        self,
        text: str,
        thamSo: "ChunkParams | Mapping[str, Any] | None" = None,
        rules: list | None = None,
    ) -> list[ChunkData]:
        if not text or not text.strip():
            return []
        params = ChunkParams.from_any(thamSo)

        starts = [m.start() for m in _HEADING_PATTERN.finditer(text)]
        if not starts:
            # No headings → fall back to size-based splitting (R17 still >= 1 chunk).
            logger.debug("structure-aware: khong co heading, fallback size-split")
            return split_by_size(text, params, {"chienLuoc": self.ten})

        # The leading part before the first heading (if it has content) is also a section.
        if starts[0] > 0:
            starts.insert(0, 0)

        boundaries = starts + [len(text)]
        chunks: list[ChunkData] = []
        for i in range(len(boundaries) - 1):
            sec_start, sec_end = boundaries[i], boundaries[i + 1]
            section = text[sec_start:sec_end]
            if not section.strip():
                continue
            tieuDe = self._extract_heading(section)
            if len(section) <= params.kichThuocMucTieu:
                chunk = make_chunk(
                    text, sec_start, sec_end, len(chunks),
                    {"chienLuoc": self.ten, "tieuDe": tieuDe},
                )
                if chunk is not None:
                    chunks.append(chunk)
            else:
                # Section too long → split further by size, keeping absolute positions.
                for sp_start, sp_end in split_spans(section, params):
                    chunk = make_chunk(
                        text, sec_start + sp_start, sec_start + sp_end, len(chunks),
                        {"chienLuoc": self.ten, "tieuDe": tieuDe},
                    )
                    if chunk is not None:
                        chunks.append(chunk)
        logger.debug("structure-aware: %d heading -> %d chunk", len(starts), len(chunks))
        return chunks

    @staticmethod
    def _extract_heading(section: str) -> str | None:
        """Get the section's first heading title (if any)."""
        first_line = section.lstrip().splitlines()[0] if section.strip() else ""
        if first_line.lstrip().startswith("#"):
            return first_line.lstrip("#").strip() or None
        return None
