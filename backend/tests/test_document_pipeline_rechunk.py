"""Unit tests for DocumentPipeline task 8.5: rechunk / editChunks / setBoundaryRules
/ resetToDefault (R5.6, R5.12, R18.2-18.11, R21.4).

Uses a FAKE Embedding_Provider + a FAKE/in-memory Vector_Store (injected via the
constructor) per project convention. Coverage:
- Rechunking replaces the old Chunks, idempotent (running twice gives the same result).
- Rechunking a DA_EMBED document -> back to DA_PARSE_CHO_DUYET + deletes vectors (R5.13).
- Failed rechunk (non-existent strategy) -> keeps the old Chunks (R18.10).
- Manual edits merge/split/adjust; reject an empty chunk -> unchanged (R18.11).
- setBoundaryRules persists the data + is APPLIED on rechunk (R18.4).
- resetToDefault restores the default strategy/parameters + deletes the custom rules (R18.6).
- A NGUOI_DUNG with GHI permission (shared) can do this themselves — no role restriction (R18.5).
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
    TrangThaiTaiLieu,
)
from app.errors import AuthorizationError, NotFoundError, ValidationError
from app.models.schemas import ChunkEditOp
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import VectorStore


# --- Fakes ------------------------------------------------------------------
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


def _tao_tai_khoan(session, email="chu@x.com", ten="chu") -> TaiKhoan:
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


def _upload(pipeline, chu, kg, noiDung=_NOI_DUNG) -> TaiLieu:
    pipeline.uploadDocument(chu, kg, noiDung.encode("utf-8"), "a.txt", "txt")
    return pipeline.db.query(TaiLieu).one()


def _fingerprint(chunks):
    """Fingerprint of (thuTu, batDau, ketThuc, noiDung) — ignores id (new each run)."""
    return [(c.thuTu, c.viTriBatDau, c.viTriKetThuc, c.noiDung) for c in chunks]


# --- rechunk idempotent + replaces everything -------------------------------
def test_rechunk_thay_sach_va_idempotent(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    idChunkCu = {c.id for c in session.query(Chunk).all()}

    lan1 = pipeline.rechunk(chu, taiLieu.id)
    lan2 = pipeline.rechunk(chu, taiLieu.id)

    # Idempotent: same noiDung/positions/order across the two rechunks.
    assert _fingerprint(lan1.chunks) == _fingerprint(lan2.chunks)
    # Replaces everything: no old chunk id remains; chunk count matches.
    idChunkMoi = {c.id for c in session.query(Chunk).all()}
    assert idChunkMoi.isdisjoint(idChunkCu)
    assert session.query(Chunk).count() == lan2.soChunk
    session.refresh(taiLieu)
    assert taiLieu.soChunk == lan2.soChunk


def test_rechunk_doi_chien_luoc(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    pipeline.rechunk(chu, taiLieu.id, chienLuocChunk="recursive")
    session.refresh(taiLieu)
    assert taiLieu.chienLuocChunk == "recursive"


# --- rechunk DA_EMBED -> back to pending review + delete vectors -----------
def test_rechunk_da_embed_quay_ve_cho_duyet_va_xoa_vector(pipeline, session, fakeClient):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    pipeline.commitEmbedding(chu, taiLieu.id)
    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED
    assert fakeClient.collections[kg.collectionName].count() == taiLieu.soChunk

    pipeline.rechunk(chu, taiLieu.id)

    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
    # Vectors cleaned up (invariant R5.13: vectors exist if and only if DA_EMBED).
    assert fakeClient.collections[kg.collectionName].count() == 0


# --- failed rechunk -> keep old Chunks --------------------------------------
def test_rechunk_that_bai_giu_nguyen_chunk_cu(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    truoc = _fingerprint(session.query(Chunk).order_by(Chunk.thuTu).all())

    # Non-existent strategy -> error at the pre-compute step, the DB has not been touched.
    with pytest.raises(ValidationError):
        pipeline.rechunk(chu, taiLieu.id, chienLuocChunk="khong-ton-tai")

    sau = _fingerprint(session.query(Chunk).order_by(Chunk.thuTu).all())
    assert sau == truoc  # old Chunks unchanged (R18.10).


# --- editChunks: merge / split / adjust -------------------------------------
def test_edit_chunks_merge(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    # Chunk small so there are multiple chunks as input for the merge.
    pipeline.rechunk(chu, taiLieu.id, thamSo=ChunkParams(kichThuocMucTieu=10, doChongLan=0))
    truoc = session.query(Chunk).order_by(Chunk.thuTu).all()
    assert len(truoc) >= 2
    noiDung0, noiDung1 = truoc[0].noiDung, truoc[1].noiDung

    ketQua = pipeline.editChunks(chu, taiLieu.id, [ChunkEditOp(loai="merge", viTri=0)])

    # Chunk 0 = merged noiDung 0 + 1; total chunk count drops by 1; thuTu is contiguous.
    assert ketQua[0].noiDung == noiDung0 + noiDung1
    assert len(ketQua) == len(truoc) - 1
    assert [c.thuTu for c in ketQua] == list(range(len(ketQua)))


def test_edit_chunks_split(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    truoc = session.query(Chunk).order_by(Chunk.thuTu).all()
    goc = truoc[0].noiDung
    cat = max(1, len(goc) // 2)

    ketQua = pipeline.editChunks(
        chu, taiLieu.id, [ChunkEditOp(loai="split", viTri=0, viTriCat=cat)]
    )

    assert ketQua[0].noiDung == goc[:cat]
    assert ketQua[1].noiDung == goc[cat:]
    assert len(ketQua) == len(truoc) + 1
    assert [c.thuTu for c in ketQua] == list(range(len(ketQua)))


def test_edit_chunks_adjust(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    # Adjust chunk 0's boundary to [0, 8) of the original text.
    ketQua = pipeline.editChunks(
        chu,
        taiLieu.id,
        [ChunkEditOp(loai="adjust", viTri=0, viTriBatDauMoi=0, viTriKetThucMoi=8)],
    )
    assert ketQua[0].viTriBatDau == 0
    assert ketQua[0].viTriKetThuc == 8
    assert ketQua[0].noiDung == _NOI_DUNG[0:8]


def test_edit_chunks_tu_choi_chunk_rong_giu_nguyen(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    truoc = _fingerprint(session.query(Chunk).order_by(Chunk.thuTu).all())

    # adjust to a whitespace-only range ("Dong mot." -> position 9 is "\n").
    with pytest.raises(ValidationError):
        pipeline.editChunks(
            chu,
            taiLieu.id,
            [ChunkEditOp(loai="adjust", viTri=0, viTriBatDauMoi=9, viTriKetThucMoi=11)],
        )

    sau = _fingerprint(session.query(Chunk).order_by(Chunk.thuTu).all())
    assert sau == truoc  # Unchanged (R18.11).


def test_edit_chunks_da_embed_quay_ve_cho_duyet(pipeline, session, fakeClient):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    pipeline.commitEmbedding(chu, taiLieu.id)

    pipeline.editChunks(chu, taiLieu.id, [ChunkEditOp(loai="adjust", viTri=0, viTriBatDauMoi=0, viTriKetThucMoi=8)])

    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
    assert fakeClient.collections[kg.collectionName].count() == 0


# --- setBoundaryRules: persist + apply on rechunk ---------------------------
def test_set_boundary_rules_luu_va_ap_khi_rechunk(pipeline, session, monkeypatch):
    import app.pipelines.document_pipeline as dp

    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    rules = [
        {"tuKhoaHoacMau": "Dieu", "dieuKien": {"loai": "heading"}},
        {"tuKhoaHoacMau": "Chuong", "dieuKien": {}},
    ]
    pipeline.setBoundaryRules(chu, "document", taiLieu.id, rules)

    # Persist data: 2 QuyTacRanhGioi records for the document.
    daLuu = session.query(QuyTacRanhGioi).filter(
        QuyTacRanhGioi.phamViId == taiLieu.id
    ).all()
    assert len(daLuu) == 2
    assert {q.tuKhoaHoacMau for q in daLuu} == {"Dieu", "Chuong"}

    # Applied on rechunk: the strategy receives the correct rules list.
    SpyChunker.received_rules = None
    monkeypatch.setattr(dp, "get_chunker", lambda ten: SpyChunker)
    pipeline.rechunk(chu, taiLieu.id, chienLuocChunk="spy")

    assert SpyChunker.received_rules is not None
    assert {q.tuKhoaHoacMau for q in SpyChunker.received_rules} == {"Dieu", "Chuong"}


def test_set_boundary_rules_thay_sach_quy_tac_cu(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    pipeline.setBoundaryRules(chu, "document", taiLieu.id, [{"tuKhoaHoacMau": "A"}])
    pipeline.setBoundaryRules(chu, "document", taiLieu.id, [{"tuKhoaHoacMau": "B"}])

    daLuu = session.query(QuyTacRanhGioi).filter(
        QuyTacRanhGioi.phamViId == taiLieu.id
    ).all()
    assert len(daLuu) == 1
    assert daLuu[0].tuKhoaHoacMau == "B"


# --- resetToDefault ---------------------------------------------------------
def test_reset_to_default_khoi_phuc_mac_dinh_va_xoa_quy_tac(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    # Change to a non-default strategy/parameters + declare custom rules.
    pipeline.rechunk(
        chu, taiLieu.id, chienLuocChunk="recursive",
        thamSo=ChunkParams(kichThuocMucTieu=300, doChongLan=20),
    )
    pipeline.setBoundaryRules(chu, "document", taiLieu.id, [{"tuKhoaHoacMau": "A"}])

    pipeline.resetToDefault(chu, taiLieu.id)

    session.refresh(taiLieu)
    # Parameters back to default; custom rules deleted.
    assert taiLieu.thamSoChunk == dataclasses.asdict(ChunkParams())
    assert (
        session.query(QuyTacRanhGioi)
        .filter(QuyTacRanhGioi.phamViId == taiLieu.id)
        .count()
        == 0
    )


# --- Permission / existence (R18.5 — GHI only, no role restriction) --------
def test_rechunk_nguoi_dung_chia_se_ghi_tu_thuc_hien(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")  # role NGUOI_DUNG
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.GHI))
    session.commit()

    # A NGUOI_DUNG with GHI permission can rechunk themselves — no code changes needed (R18.5/8/9).
    ketQua = pipeline.rechunk(khach, taiLieu.id)
    assert ketQua.soChunk >= 1


def test_rechunk_chi_doc_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.CHI_DOC))
    session.commit()

    with pytest.raises(AuthorizationError):
        pipeline.rechunk(khach, taiLieu.id)


def test_rechunk_tai_lieu_khong_ton_tai(pipeline, session):
    chu = _tao_tai_khoan(session)
    _tao_khong_gian(session, chu)
    with pytest.raises(NotFoundError):
        pipeline.rechunk(chu, "khong-ton-tai")
