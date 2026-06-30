"""Unit tests for task 8.12: a NGUOI_DUNG self-serves their own chunking WITHOUT code
changes (R18.5, R18.8, R18.9).

Focus (distinct from `test_document_pipeline_rechunk.py` — which checks each individual
operation + 1 NGUOI_DUNG-with-GHI rechunk case + 1 CHI_DOC rejection case): here we check
the ENTIRE self-serve workflow END-TO-END for ONE VaiTro.NGUOI_DUNG account (NOT a
QUAN_TRI), using only configuration/data:

    select/override strategy + parameters -> rechunk -> editChunks (merge/split/adjust)
    -> setBoundaryRules (APPLIED on rechunk) -> resetToDefault

for both capacities:
- NGUOI_DUNG as the workspace OWNER (not an admin).
- NGUOI_DUNG with only GHI permission via a share (not owner, not admin).

Asserted invariant: NO step in the workflow requires VaiTro == QUAN_TRI — every account
keeps VaiTro.NGUOI_DUNG and can still perform all of it.

Uses a FAKE Embedding_Provider + a FAKE/in-memory Vector_Store (injected via the
constructor) per project convention.
"""

from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.base import ChunkData, ChunkParams
from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import (
    ChiaSe,
    Chunk,
    HanMuc,
    KhongGianTaiLieu,
    MucQuyen,
    QuyTacRanhGioi,
    TaiKhoan,
    TaiLieu,
    VaiTro,
)
from app.models.schemas import ChunkEditOp
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import VectorStore


# --- Fakes (same shape as the other pipeline tests) -------------------------
class FakeEmbeddingProvider:
    ten = "fake"

    def embed(self, texts):
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class FakeCollection:
    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: dict[str, tuple] = {}

    def add(self, *, ids, embeddings, documents, metadatas) -> None:
        for i, vec, doc, meta in zip(ids, embeddings, documents, metadatas):
            self.rows[i] = (vec, doc, dict(meta))

    def delete(self, *, where=None, ids=None) -> None:
        if ids is not None:
            for i in ids:
                self.rows.pop(i, None)
        if where:
            for i in [
                i
                for i, (_, _, meta) in self.rows.items()
                if all(meta.get(k) == v for k, v in where.items())
            ]:
                self.rows.pop(i, None)

    def count(self) -> int:
        return len(self.rows)


class FakeClient:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def get_or_create_collection(self, name: str) -> FakeCollection:
        col = self.collections.get(name)
        if col is None:
            col = FakeCollection(name)
            self.collections[name] = col
        return col

    def delete_collection(self, name: str) -> None:
        if name not in self.collections:
            raise ValueError(f"Collection '{name}' khong ton tai")
        del self.collections[name]


class SpyChunker:
    """Fake strategy: records the `rules` it received; returns 1 chunk covering the whole text."""

    received_rules = None

    def chunk(self, text, thamSo=None, rules=None):
        SpyChunker.received_rules = rules
        return [ChunkData(thuTu=0, viTriBatDau=0, viTriKetThuc=len(text), noiDung=text)]


# --- Fixtures ---------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _nap_chunker():
    discover_chunkers()


@pytest.fixture()
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def fakeClient():
    return FakeClient()


@pytest.fixture()
def pipeline(session, fakeClient):
    return DocumentPipeline(
        session,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=FakeEmbeddingProvider(),
    )


def _tao_tai_khoan(session, email, ten) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    tk.hanMuc = HanMuc()
    session.add(tk)
    session.commit()
    return tk


def _tao_khong_gian(session, chuSoHuu, ten="KG") -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten=ten,
        moTa="",
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    session.add(kg)
    session.flush()
    kg.collectionName = f"ws_{kg.id}"
    session.commit()
    return kg


_NOI_DUNG = "Dong mot.\n\nDong hai.\n\nDong ba."


def _upload(pipeline, chu, kg) -> TaiLieu:
    pipeline.uploadDocument(chu, kg, _NOI_DUNG.encode("utf-8"), "a.txt", "txt")
    return pipeline.db.query(TaiLieu).one()


def _run_self_serve_workflow(pipeline, session, actor, taiLieu, monkeypatch):
    """Run the entire self-serve workflow and assert each step succeeds.

    `actor` MUST be VaiTro.NGUOI_DUNG (not QUAN_TRI). If any step gates on the admin
    role, one of the calls below will raise AuthorizationError and the test fails —
    proving the R18.5/8/9 behavior.
    """
    # Premise: actor is a regular NGUOI_DUNG, with NO admin privileges.
    assert actor.vaiTro == VaiTro.NGUOI_DUNG
    assert actor.vaiTro != VaiTro.QUAN_TRI

    # 1) Select/override strategy + parameters then rechunk (R18.7, R18.8).
    ketQua = pipeline.rechunk(
        actor,
        taiLieu.id,
        chienLuocChunk="recursive",
        thamSo=ChunkParams(kichThuocMucTieu=10, doChongLan=0),
    )
    assert ketQua.soChunk >= 2  # small params -> many chunks to feed the merge
    session.refresh(taiLieu)
    assert taiLieu.chienLuocChunk == "recursive"

    # 2) editChunks: merge -> split -> adjust, all self-servable (R18.3).
    truoc = pipeline.db.query(Chunk).filter(Chunk.taiLieuId == taiLieu.id).count()
    sauMerge = pipeline.editChunks(actor, taiLieu.id, [ChunkEditOp(loai="merge", viTri=0)])
    assert len(sauMerge) == truoc - 1

    goc = sauMerge[0].noiDung
    cat = max(1, len(goc) // 2)
    sauSplit = pipeline.editChunks(
        actor, taiLieu.id, [ChunkEditOp(loai="split", viTri=0, viTriCat=cat)]
    )
    assert sauSplit[0].noiDung == goc[:cat]
    assert sauSplit[1].noiDung == goc[cat:]

    sauAdjust = pipeline.editChunks(
        actor,
        taiLieu.id,
        [ChunkEditOp(loai="adjust", viTri=0, viTriBatDauMoi=0, viTriKetThucMoi=8)],
    )
    assert sauAdjust[0].viTriBatDau == 0
    assert sauAdjust[0].viTriKetThuc == 8

    # 3) setBoundaryRules (persist DATA) + APPLIED on rechunk (R18.4).
    import app.pipelines.document_pipeline as dp

    pipeline.setBoundaryRules(
        actor,
        "document",
        taiLieu.id,
        [{"tuKhoaHoacMau": "Dieu", "dieuKien": {"loai": "heading"}}],
    )
    daLuu = (
        session.query(QuyTacRanhGioi)
        .filter(QuyTacRanhGioi.phamViId == taiLieu.id)
        .all()
    )
    assert {q.tuKhoaHoacMau for q in daLuu} == {"Dieu"}

    SpyChunker.received_rules = None
    monkeypatch.setattr(dp, "get_chunker", lambda ten: SpyChunker)
    pipeline.rechunk(actor, taiLieu.id, chienLuocChunk="spy")
    assert SpyChunker.received_rules is not None
    assert {q.tuKhoaHoacMau for q in SpyChunker.received_rules} == {"Dieu"}
    monkeypatch.undo()  # restore the real get_chunker for the reset step

    # 4) resetToDefault: parameters back to default + delete custom rules (R18.6). Note:
    # reset rechunks with "auto" -> the SAVED strategy is the resolved name (e.g.
    # "recursive" for plain text, not the string "auto").
    pipeline.resetToDefault(actor, taiLieu.id)
    session.refresh(taiLieu)
    assert taiLieu.thamSoChunk == dataclasses.asdict(ChunkParams())
    assert (
        session.query(QuyTacRanhGioi)
        .filter(QuyTacRanhGioi.phamViId == taiLieu.id)
        .count()
        == 0
    )

    # Postcondition: actor is still NGUOI_DUNG after the whole workflow (no need to
    # escalate the role to self-serve).
    assert actor.vaiTro == VaiTro.NGUOI_DUNG


def test_chu_so_huu_nguoi_dung_tu_phuc_vu_toan_bo_workflow(pipeline, session, monkeypatch):
    """The OWNER is a NGUOI_DUNG (not an admin) running the entire workflow (R18.5/8/9)."""
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    assert chu.vaiTro == VaiTro.NGUOI_DUNG  # the default on registration
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    _run_self_serve_workflow(pipeline, session, chu, taiLieu, monkeypatch)


def test_nguoi_dung_chia_se_ghi_tu_phuc_vu_toan_bo_workflow(pipeline, session, monkeypatch):
    """A NGUOI_DUNG with only GHI permission (not owner, not admin) also runs the entire workflow."""
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.GHI))
    session.commit()

    # khach: does not own the workspace, is not an admin — only shared with GHI.
    assert khach.vaiTro == VaiTro.NGUOI_DUNG
    assert kg.chuSoHuuId != khach.id

    _run_self_serve_workflow(pipeline, session, khach, taiLieu, monkeypatch)
