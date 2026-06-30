"""AutoSelector: selects a ChienLuocChunk using a FIXED priority order (R17.3-17.8).

When KhongGianTaiLieu / TaiLieu sets the strategy to "auto", the Document_Pipeline
(task 8) calls `AutoSelector.selectStrategy(...)` to decide the concrete strategy
based on the document's structural cues, following an UNCHANGING priority order:

    1) Has "Điều" + a leading digit  -> "vietnamese-law" (regardless of other cues, R17.4)
    2) Not (1) but has a markdown heading -> "structure-aware" (R17.5)
    3) Not (1)(2), is a paginated PDF (has form-feed marker) -> "page" (R17.6)
    4) Otherwise -> "recursive" (default, R17.8)

Reuses each strategy's own detection patterns (DIEU_BOUNDARY_PATTERN,
_HEADING_PATTERN, _PAGE_MARKER) so the cues here ALWAYS match how that strategy
actually splits — avoiding duplicated or divergent logic.

AutoSelector only returns strategy names that ACTUALLY exist in the registry. When a
strategy name (specified by configuration) does not exist, `resolveChunker` delegates
to `get_chunker`, which raises `ValidationError` for a clearly invalid name (R17.7).
"""

from __future__ import annotations

import logging

from app.chunking.page_chunker import _PAGE_MARKER
from app.chunking.registry import get_chunker
from app.chunking.structure_chunker import _HEADING_PATTERN
from app.chunking.vietnamese_law_chunker import DIEU_BOUNDARY_PATTERN

logger = logging.getLogger(__name__)


class AutoSelector:
    """Selects a chunking strategy using a fixed priority order (R17.3-17.8).

    Signature contract used by the Document_Pipeline:
        selectStrategy(taiLieu_or_text, tomTat=None, dinhDang=None) -> str
    - `taiLieu_or_text`: the document text (str) OR an object that has a `noiDung`
      attribute (e.g. a TaiLieu preview). The pipeline layer usually passes the text
      directly.
    - `tomTat`: the document summary (optional, not currently used for selection but
      kept for compatibility with the design and future extension).
    - `dinhDang`: the document format (e.g. "pdf") used to detect pagination cues.
    """

    #: FIXED priority order (for reference/documentation only; the logic below follows it).
    PRIORITY = ["vietnamese-law", "structure-aware", "page", "recursive"]

    def selectStrategy(
        self,
        taiLieu_or_text: object,
        tomTat: object | None = None,
        dinhDang: str | None = None,
    ) -> str:
        """Return the NAME of the strategy selected by the fixed priority order."""
        text = self._extract_text(taiLieu_or_text)

        if self._has_dieu_boundary(text):
            chosen = "vietnamese-law"
        elif self._has_markdown_heading(text):
            chosen = "structure-aware"
        elif self._is_paginated_pdf(text, dinhDang):
            chosen = "page"
        else:
            chosen = "recursive"

        logger.info("AutoSelector chon chien luoc '%s' (dinhDang=%s)", chosen, dinhDang)
        return chosen

    def resolveChunker(self, ten: str):
        """Resolve a strategy NAME -> a ChienLuocChunk instance.

        Delegates to `get_chunker`: a name not present in the registry raises
        ValidationError for a clearly invalid name (R17.7). Guarantees every name
        AutoSelector returns can be resolved.
        """
        cls = get_chunker(ten)
        logger.debug("AutoSelector phan giai chien luoc '%s' -> %s", ten, cls.__name__)
        return cls()

    @staticmethod
    def _extract_text(taiLieu_or_text: object) -> str:
        """Get the text from a str or from an object with a `noiDung` attribute."""
        if isinstance(taiLieu_or_text, str):
            return taiLieu_or_text
        noiDung = getattr(taiLieu_or_text, "noiDung", None)
        return noiDung if isinstance(noiDung, str) else ""

    @staticmethod
    def _has_dieu_boundary(text: str) -> bool:
        """Whether there is an "Điều" + digit + ./: line (an Article boundary) (R17.4)."""
        return bool(text) and DIEU_BOUNDARY_PATTERN.search(text) is not None

    @staticmethod
    def _has_markdown_heading(text: str) -> bool:
        """Whether there is a markdown heading line (`#`..`######`) (R17.5)."""
        return bool(text) and _HEADING_PATTERN.search(text) is not None

    @staticmethod
    def _is_paginated_pdf(text: str, dinhDang: str | None) -> bool:
        """A paginated PDF: has a form-feed marker and a non-conflicting format (R17.6)."""
        if not text or _PAGE_MARKER not in text:
            return False
        return dinhDang is None or dinhDang.strip().lower() == "pdf"
