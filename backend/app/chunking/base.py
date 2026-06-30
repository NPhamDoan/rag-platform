"""ChunkerBase + lightweight Chunk data type + shared splitting helpers (R15, R17).

Every ChienLuocChunk inherits `ChunkerBase` and implements `chunk(text, thamSo, rules)`,
returning a list of `ChunkData` (NOT ORM rows — the Document_Pipeline maps `ChunkData`
to the ORM `Chunk` at a higher layer).

Core invariants (R15.1, R15.2) that EVERY strategy must guarantee:
- Non-empty text (after stripping) → produces >= 1 ChunkData, each with non-empty
  `noiDung`.
- Empty text / whitespace-only → returns an empty list (0 chunks); the pipeline layer
  rejects 0-chunk documents.

Splitting parameters (target size, overlap) are passed via `ChunkParams` (a dataclass
with sensible defaults). The shared splitting helpers (`_split_spans`, `make_chunk`)
live here so every strategy can reuse them, avoiding duplicated size-based splitting
logic.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# Defaults inherited from the legacy law chunker: ~1200 chars / chunk, 150-char overlap.
KICH_THUOC_MUC_TIEU_MAC_DINH = 1200
DO_CHONG_LAN_MAC_DINH = 150


@dataclass(frozen=True)
class ChunkParams:
    """Parameters controlling how to split: target size + overlap (in characters)."""

    kichThuocMucTieu: int = KICH_THUOC_MUC_TIEU_MAC_DINH
    doChongLan: int = DO_CHONG_LAN_MAC_DINH

    def __post_init__(self) -> None:
        if self.kichThuocMucTieu <= 0:
            raise ValueError("kichThuocMucTieu phai > 0")
        if self.doChongLan < 0:
            raise ValueError("doChongLan khong duoc am")
        if self.doChongLan >= self.kichThuocMucTieu:
            raise ValueError("doChongLan phai nho hon kichThuocMucTieu")

    @classmethod
    def from_any(cls, thamSo: "ChunkParams | Mapping[str, Any] | None") -> "ChunkParams":
        """Normalize the input parameters to ChunkParams (accepts None / dict / instance)."""
        if thamSo is None:
            return cls()
        if isinstance(thamSo, ChunkParams):
            return thamSo
        if isinstance(thamSo, Mapping):
            return cls(
                kichThuocMucTieu=int(
                    thamSo.get("kichThuocMucTieu", KICH_THUOC_MUC_TIEU_MAC_DINH)
                ),
                doChongLan=int(thamSo.get("doChongLan", DO_CHONG_LAN_MAC_DINH)),
            )
        raise TypeError(f"thamSo khong hop le: {type(thamSo)!r}")


@dataclass
class ChunkData:
    """Lightweight split result (not yet ORM). The pipeline layer maps it to the `Chunk` ORM."""

    thuTu: int
    viTriBatDau: int
    viTriKetThuc: int
    noiDung: str
    metadata: dict[str, Any] = field(default_factory=dict)


def make_chunk(
    text: str,
    start: int,
    end: int,
    thuTu: int,
    metadata: dict[str, Any] | None = None,
) -> ChunkData | None:
    """Build a ChunkData from the span [start, end) of `text`, with surrounding whitespace trimmed.

    Returns None if the span contains only whitespace (so it can be dropped from the
    result) — guaranteeing the non-empty `noiDung` invariant (R15.2). The start/end
    positions are adjusted to the stripped portion so they remain accurate against the
    original text.
    """
    raw = text[start:end]
    leading = len(raw) - len(raw.lstrip())
    trailing = len(raw) - len(raw.rstrip())
    new_start = start + leading
    new_end = end - trailing
    noiDung = text[new_start:new_end]
    if not noiDung:
        return None
    return ChunkData(
        thuTu=thuTu,
        viTriBatDau=new_start,
        viTriKetThuc=new_end,
        noiDung=noiDung,
        metadata=metadata or {},
    )


# Boundary priority order for size-based splitting (paragraph → line → sentence → word).
_SEPARATORS = ("\n\n", "\n", ". ", " ")


def split_spans(text: str, thamSo: ChunkParams) -> list[tuple[int, int]]:
    """Split `text` into spans [start, end) <= kichThuocMucTieu, with overlap.

    Tries to break at the nearest natural boundary (paragraph/line/sentence/word)
    before the size limit; if no boundary is found, breaks hard at the limit. The
    positions are always accurate against the original `text`.
    """
    n = len(text)
    if n == 0:
        return []
    kichThuoc = thamSo.kichThuocMucTieu
    doChongLan = thamSo.doChongLan
    spans: list[tuple[int, int]] = []
    start = 0
    while start < n:
        end = min(start + kichThuoc, n)
        if end < n:
            break_at = -1
            for sep in _SEPARATORS:
                idx = text.rfind(sep, start, end)
                if idx > start:
                    break_at = idx + len(sep)
                    break
            if break_at > start:
                end = break_at
        spans.append((start, end))
        if end >= n:
            break
        nxt = end - doChongLan
        start = nxt if nxt > start else end
    return spans


def chunks_from_spans(
    text: str,
    spans: list[tuple[int, int]],
    metadata_fn=None,
) -> list[ChunkData]:
    """Build a list of ChunkData from the spans, assigning sequential `thuTu`, dropping empty spans.

    `metadata_fn(index, start, end)` (optional) returns a metadata dict for each chunk.
    """
    chunks: list[ChunkData] = []
    for start, end in spans:
        thuTu = len(chunks)
        meta = metadata_fn(thuTu, start, end) if metadata_fn else None
        chunk = make_chunk(text, start, end, thuTu, meta)
        if chunk is not None:
            chunks.append(chunk)
    return chunks


def split_by_size(
    text: str, thamSo: ChunkParams, base_metadata: dict[str, Any] | None = None
) -> list[ChunkData]:
    """Split by size (used as the shared fallback for every strategy)."""
    spans = split_spans(text, thamSo)
    meta_fn = (lambda i, s, e: dict(base_metadata)) if base_metadata else None
    return chunks_from_spans(text, spans, meta_fn)


class ChunkerBase(ABC):
    """Common interface for every ChienLuocChunk.

    `chunk(text, thamSo, rules)` returns list[ChunkData]. Subclasses must guarantee the
    R15.1/R15.2 invariants (non-empty text → >= 1 ChunkData with non-empty noiDung;
    empty → []). `rules` (QuyTacRanhGioi) is accepted for interface compatibility, but a
    strategy may ignore it if it does not apply.
    """

    #: Strategy name registered in the registry (subclasses set it for easier tracing/logging).
    ten: str = ""

    @abstractmethod
    def chunk(
        self,
        text: str,
        thamSo: "ChunkParams | Mapping[str, Any] | None" = None,
        rules: list | None = None,
    ) -> list[ChunkData]:
        """Split `text` into a list of ChunkData using the concrete strategy."""
        raise NotImplementedError
