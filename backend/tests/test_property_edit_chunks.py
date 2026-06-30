"""Property-based test for manual Chunk editing (Property 25, task 8.8).

# Feature: multi-user-rag-platform, Property 25: Manual Chunk editing preserves content
# and rejects empty Chunks.
#   (a) Content preservation — applying a merge/split operation (without creating an
#       empty chunk) KEEPS the total concatenated text of the chunks unchanged
#       (concat(noiDung) is invariant); merge joins chunk[i]+chunk[i+1], split keeps
#       left+right == the original chunk. After editing, `thuTu` is contiguous 0..N-1.
#   (b) Rejecting empty chunks — an operation that produces a whitespace-only chunk
#       (split at position 0/len, or adjust to a whitespace-only range) raises
#       `ValidationError` and KEEPS the old chunks unchanged (fingerprint unchanged).
# Validates: Requirements 18.3, 18.11

Manual editing does NOT embed (the document is in DA_PARSE_CHO_DUYET) so the
Vector_Store is untouched; we still use a FAKE Embedding_Provider + FAKE/in-memory
Vector_Store (reused from tests/test_document_pipeline_rechunk.py) per project
convention. Each example uses a NEW in-memory SQLite. Text is generated from a list of
(alphanumeric) words joined with "\\n\\n" so it has both non-whitespace content and
whitespace (for the empty-adjust scenario too). max_examples=100.
"""

from __future__ import annotations

import re
import string

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.base import ChunkParams
from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import Chunk, HanMuc, KhongGianTaiLieu, TaiKhoan, TaiLieu
from app.errors import ValidationError
from app.models.schemas import ChunkEditOp
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import VectorStore
from tests.test_document_pipeline_rechunk import FakeClient, FakeEmbeddingProvider


# --- Load the chunker registry once ----------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _nap_chunker():
    discover_chunkers()


# --- Text generation: a list of alphanumeric words joined with "\n\n" -------
_TU = st.text(alphabet=string.ascii_letters + string.digits, min_size=1, max_size=12)
_DANH_SACH_TU = st.lists(_TU, min_size=1, max_size=8)


# --- Helpers ----------------------------------------------------------------
def _setup(text: str):
    """A NEW in-memory SQLite + account + space + a finely-chunked document.

    Chunked finely (kichThuocMucTieu=10) to get many chunks as input for merge/split.
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()

    tk = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    tk.hanMuc = HanMuc()
    db.add(tk)
    db.commit()
    kg = KhongGianTaiLieu(
        ten="KG",
        moTa="",
        chuSoHuuId=tk.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    db.add(kg)
    db.flush()
    kg.collectionName = f"ws_{kg.id}"
    db.commit()

    pipeline = DocumentPipeline(
        db,
        vectorStore=VectorStore(client=FakeClient()),
        embeddingProvider=FakeEmbeddingProvider(),
    )
    pipeline.uploadDocument(tk, kg, text.encode("utf-8"), "a.txt", "txt")
    taiLieu = db.query(TaiLieu).one()
    pipeline.rechunk(tk, taiLieu.id, thamSo=ChunkParams(kichThuocMucTieu=10, doChongLan=0))
    return engine, db, pipeline, tk, taiLieu


def _chunks(db, taiLieuId):
    return (
        db.query(Chunk)
        .filter(Chunk.taiLieuId == taiLieuId)
        .order_by(Chunk.thuTu)
        .all()
    )


def _fingerprint(chunks):
    """Fingerprint (thuTu, batDau, ketThuc, noiDung) — ignoring id (id differs each time)."""
    return [(c.thuTu, c.viTriBatDau, c.viTriKetThuc, c.noiDung) for c in chunks]


def _concat(chunks):
    """Total concatenated text of the chunks in order (content-preservation invariant)."""
    return "".join(c.noiDung for c in chunks)


def _cat_hop_le(noiDung: str) -> int | None:
    """Return a split position within (0, len) such that both parts contain a non-whitespace char.

    Returns None if not possible (the chunk has < 2 non-whitespace characters)."""
    nonws = [i for i, ch in enumerate(noiDung) if not ch.isspace()]
    if len(nonws) < 2:
        return None
    cat = nonws[-1]  # left holds the first non-whitespace char; right holds the last char.
    if 0 < cat < len(noiDung):
        return cat
    return None


def _khoang_trang_span(text: str) -> tuple[int, int] | None:
    """Return the (start, end) of a whitespace-only span within `text`, or None."""
    m = re.search(r"\s+", text)
    return (m.start(), m.end()) if m else None


# --- Property 25 ------------------------------------------------------------
@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(
    tu=_DANH_SACH_TU,
    scenario=st.sampled_from(["merge", "split_valid", "split_invalid", "adjust_invalid"]),
    data=st.data(),
)
def test_sua_tay_chunk_bao_toan_noi_dung_va_tu_choi_chunk_rong(tu, scenario, data):
    text = "\n\n".join(tu)
    assume(text.strip())  # whitespace-only text -> 0 chunks, out of scope.

    engine, db, pipeline, tk, taiLieu = _setup(text)
    try:
        truoc = _chunks(db, taiLieu.id)
        n = len(truoc)
        assume(n >= 1)
        concatTruoc = _concat(truoc)
        fpTruoc = _fingerprint(truoc)

        if scenario == "merge":
            # (a) Preservation: join chunk i with i+1; total concat unchanged; one fewer chunk.
            assume(n >= 2)
            i = data.draw(st.integers(min_value=0, max_value=n - 2))
            ketQua = pipeline.editChunks(
                tk, taiLieu.id, [ChunkEditOp(loai="merge", viTri=i)]
            )
            assert _concat(ketQua) == concatTruoc
            assert len(ketQua) == n - 1
            assert [c.thuTu for c in ketQua] == list(range(len(ketQua)))

        elif scenario == "split_valid":
            # (a) Preservation: split one chunk into left+right; total concat unchanged.
            ungVien = [
                idx for idx, c in enumerate(truoc) if _cat_hop_le(c.noiDung) is not None
            ]
            assume(ungVien)
            i = data.draw(st.sampled_from(ungVien))
            cat = _cat_hop_le(truoc[i].noiDung)
            ketQua = pipeline.editChunks(
                tk, taiLieu.id, [ChunkEditOp(loai="split", viTri=i, viTriCat=cat)]
            )
            assert _concat(ketQua) == concatTruoc
            assert len(ketQua) == n + 1
            assert [c.thuTu for c in ketQua] == list(range(len(ketQua)))

        elif scenario == "split_invalid":
            # (b) Rejection: split at position 0 or len -> ValidationError, kept unchanged.
            i = data.draw(st.integers(min_value=0, max_value=n - 1))
            content = truoc[i].noiDung
            cat = data.draw(st.sampled_from([0, len(content)]))
            with pytest.raises(ValidationError):
                pipeline.editChunks(
                    tk, taiLieu.id, [ChunkEditOp(loai="split", viTri=i, viTriCat=cat)]
                )
            assert _fingerprint(_chunks(db, taiLieu.id)) == fpTruoc

        else:  # adjust_invalid
            # (b) Rejection: adjust to a whitespace-only range -> ValidationError.
            span = _khoang_trang_span(text)
            assume(span is not None)
            s, e = span
            with pytest.raises(ValidationError):
                pipeline.editChunks(
                    tk,
                    taiLieu.id,
                    [ChunkEditOp(loai="adjust", viTri=0, viTriBatDauMoi=s, viTriKetThucMoi=e)],
                )
            assert _fingerprint(_chunks(db, taiLieu.id)) == fpTruoc
    finally:
        db.close()
        engine.dispose()
