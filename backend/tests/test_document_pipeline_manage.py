"""Unit tests for DocumentPipeline task 8.10: buildSummary / listDocuments /
deleteDocument (R5.7, R5.8, R5.9, R5.10).

Uses a FAKE Embedding_Provider + a FAKE/in-memory Vector_Store (injected via the
constructor) per project convention. Coverage:
- buildSummary: creates a TomTatTaiLieu (summary + outline from structural signals), upsert.
- commitEmbedding generates a TomTatTaiLieu on a successful load (R5.10).
- listDocuments: correct pagination + total count; permission control (>= CHI_DOC);
  validates page/pageSize.
- deleteDocument: deletes the document + Chunks + TomTat + vectors; 404 when not found;
  non-CHI_DOC cannot list; non-GHI cannot delete.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import (
    ChiaSe,
    Chunk,
    HanMuc,
    KhongGianTaiLieu,
    MucQuyen,
    TaiKhoan,
    TaiLieu,
    TomTatTaiLieu,
    TrangThaiTaiLieu,
)
from app.errors import AuthorizationError, NotFoundError, ValidationError
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


def _upload(pipeline, chu, kg, noiDung=_NOI_DUNG, tenFile="a.txt") -> TaiLieu:
    pipeline.uploadDocument(chu, kg, noiDung.encode("utf-8"), tenFile, "txt")
    return (
        pipeline.db.query(TaiLieu)
        .filter(TaiLieu.tenFile == tenFile)
        .order_by(TaiLieu.createdAt.desc())
        .first()
    )


# --- buildSummary -----------------------------------------------------------
def test_build_summary_tao_tom_tat_va_outline_tu_dieu(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    noiDung = (
        "Điều 1. Phạm vi điều chỉnh\nNoi dung dieu mot.\n\n"
        "Điều 2. Đối tượng áp dụng\nNoi dung dieu hai."
    )
    taiLieu = _upload(pipeline, chu, kg, noiDung=noiDung)

    tomTat = pipeline.buildSummary(taiLieu)

    # Persisted in the DB (1-1 with TaiLieu).
    assert session.get(TomTatTaiLieu, taiLieu.id) is not None
    assert tomTat.tomTat  # summary not empty
    # Outline taken from the "Điều N." lines in ascending position order.
    tieuDe = [m["tieuDe"] for m in tomTat.outline]
    assert any(t.startswith("Điều 1.") for t in tieuDe)
    assert any(t.startswith("Điều 2.") for t in tieuDe)
    viTri = [m["viTri"] for m in tomTat.outline]
    assert viTri == sorted(viTri)


def test_build_summary_outline_tu_heading_markdown(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    noiDung = "# Tieu de chinh\nGioi thieu.\n\n## Muc con\nChi tiet muc con."
    taiLieu = _upload(pipeline, chu, kg, noiDung=noiDung)

    tomTat = pipeline.buildSummary(taiLieu)

    tieuDe = {m["tieuDe"] for m in tomTat.outline}
    assert "Tieu de chinh" in tieuDe
    assert "Muc con" in tieuDe  # the '#' has been stripped


def test_build_summary_fallback_theo_chunk_khi_khong_co_dau_hieu(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    # Plain text, no "Điều", no headings → outline by Chunk boundaries.
    taiLieu = _upload(pipeline, chu, kg, noiDung="Cau mot. Cau hai. Cau ba.")

    tomTat = pipeline.buildSummary(taiLieu)

    soChunk = session.query(Chunk).filter(Chunk.taiLieuId == taiLieu.id).count()
    assert len(tomTat.outline) == soChunk
    assert all("tieuDe" in m and "viTri" in m for m in tomTat.outline)


def test_build_summary_upsert_cap_nhat_khong_trung_khoa(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    pipeline.buildSummary(taiLieu)
    pipeline.buildSummary(taiLieu)  # the second time must not collide on the primary key

    assert session.query(TomTatTaiLieu).filter(
        TomTatTaiLieu.taiLieuId == taiLieu.id
    ).count() == 1


def test_commit_embedding_sinh_tom_tat(pipeline, session):
    """R5.10: a successful load (DA_EMBED) → has a TomTatTaiLieu."""
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)

    pipeline.commitEmbedding(chu, taiLieu.id)

    assert session.get(TomTatTaiLieu, taiLieu.id) is not None


# --- listDocuments: phan trang + tong so ------------------------------------
def test_list_documents_phan_trang_va_tong_so(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    for i in range(5):
        _upload(pipeline, chu, kg, tenFile=f"f{i}.txt")

    trang1 = pipeline.listDocuments(chu, kg.id, page=1, pageSize=2)
    trang2 = pipeline.listDocuments(chu, kg.id, page=2, pageSize=2)
    trang3 = pipeline.listDocuments(chu, kg.id, page=3, pageSize=2)

    # Total = 5 on every page; correct page size.
    assert trang1.tongSo == trang2.tongSo == trang3.tongSo == 5
    assert len(trang1.items) == 2
    assert len(trang2.items) == 2
    assert len(trang3.items) == 1  # last page has the leftover 1
    # No overlap between pages.
    ids = (
        [d.id for d in trang1.items]
        + [d.id for d in trang2.items]
        + [d.id for d in trang3.items]
    )
    assert len(set(ids)) == 5


def test_list_documents_mac_dinh_page_size_20(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    _upload(pipeline, chu, kg)

    ketQua = pipeline.listDocuments(chu, kg.id)
    assert ketQua.page == 1
    assert ketQua.pageSize == 20
    assert ketQua.tongSo == 1
    assert len(ketQua.items) == 1


def test_list_documents_truong_dto_phan_anh_tai_lieu(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg, tenFile="bao_cao.txt")

    item = pipeline.listDocuments(chu, kg.id).items[0]
    assert item.id == taiLieu.id
    assert item.tenFile == "bao_cao.txt"
    assert item.dinhDang == "txt"
    assert item.soChunk == taiLieu.soChunk
    assert item.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET


@pytest.mark.parametrize("page,pageSize", [(0, 20), (-1, 20), (1, 0), (1, 101), (1, -5)])
def test_list_documents_tham_so_ngoai_khoang_bi_tu_choi(pipeline, session, page, pageSize):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    _upload(pipeline, chu, kg)

    with pytest.raises(ValidationError):
        pipeline.listDocuments(chu, kg.id, page=page, pageSize=pageSize)


def test_list_documents_chia_se_chi_doc_xem_duoc(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.CHI_DOC))
    session.commit()

    ketQua = pipeline.listDocuments(khach, kg.id)
    assert ketQua.tongSo == 1


def test_list_documents_khong_co_quyen_404(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")  # no permission
    kg = _tao_khong_gian(session, chu)
    _upload(pipeline, chu, kg)

    with pytest.raises(NotFoundError):
        pipeline.listDocuments(khach, kg.id)


def test_list_documents_khong_gian_khong_ton_tai_404(pipeline, session):
    chu = _tao_tai_khoan(session)
    with pytest.raises(NotFoundError):
        pipeline.listDocuments(chu, "khong-ton-tai")


# --- deleteDocument ---------------------------------------------------------
def test_delete_document_xoa_tai_lieu_chunk_va_vector(pipeline, session, fakeClient):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    pipeline.commitEmbedding(chu, taiLieu.id)
    assert fakeClient.collections[kg.collectionName].count() == taiLieu.soChunk

    pipeline.deleteDocument(chu, taiLieu.id)

    # The document + Chunks + TomTat have been removed from the RDB.
    assert session.get(TaiLieu, taiLieu.id) is None
    assert session.query(Chunk).filter(Chunk.taiLieuId == taiLieu.id).count() == 0
    assert session.get(TomTatTaiLieu, taiLieu.id) is None
    # The vectors have been removed from the Vector_Store.
    assert fakeClient.collections[kg.collectionName].count() == 0


def test_delete_document_khong_ton_tai_404(pipeline, session, fakeClient):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    pipeline.commitEmbedding(chu, taiLieu.id)
    soVectorTruoc = fakeClient.collections[kg.collectionName].count()

    with pytest.raises(NotFoundError):
        pipeline.deleteDocument(chu, "khong-ton-tai")

    # The Vector_Store is unchanged (R5.9).
    assert fakeClient.collections[kg.collectionName].count() == soVectorTruoc


def test_delete_document_chi_doc_bi_tu_choi(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.CHI_DOC))
    session.commit()

    with pytest.raises(AuthorizationError):
        pipeline.deleteDocument(khach, taiLieu.id)
    # The document still exists.
    assert session.get(TaiLieu, taiLieu.id) is not None


def test_delete_document_quyen_ghi_chia_se_thanh_cong(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.GHI))
    session.commit()

    pipeline.deleteDocument(khach, taiLieu.id)
    assert session.get(TaiLieu, taiLieu.id) is None
