"""Unit tests for DocumentPipeline.commitEmbedding (task 8.4, R5.13, R21.1, R21.4).

Uses a FAKE Embedding_Provider + a FAKE/in-memory Vector_Store (injected via the
constructor) per project convention: LLM/embedding providers and the vector store are
mocked in tests.

Coverage:
- Commit embed: DA_PARSE_CHO_DUYET -> DA_EMBED + writes vectors (R5.13).
- Committing a document already DA_EMBED / in a wrong state -> ValidationError, nothing changes.
- No GHI permission -> AuthorizationError.
- Non-existent document -> NotFoundError.
- Embedding error -> state unchanged, leaves NO vectors behind (invariant R5.13).
- Resolves the Embedding_Provider for the workspace via the registry when not injected.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import (
    ChiaSe,
    HanMuc,
    KhongGianTaiLieu,
    MucQuyen,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
)
from app.errors import AuthorizationError, NotFoundError, ValidationError
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, VectorStore


# --- Fakes ------------------------------------------------------------------
class FakeEmbeddingProvider:
    """Fake Embedding_Provider: a fixed vector based on the text length (deterministic)."""

    ten = "fake"

    def __init__(self, *, loi: bool = False) -> None:
        self.loi = loi
        self.soLanGoi = 0

    def embed(self, texts):
        self.soLanGoi += 1
        if self.loi:
            raise RuntimeError("Embedding that bai (gia lap)")
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class FakeCollection:
    """In-memory collection: stores id -> (vector, document, metadata)."""

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
    """In-memory client: manages FakeCollection instances by name."""

    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}
        self.addLoiTrenCollection: str | None = None  # inject an error when adding into this name

    def get_or_create_collection(self, name: str) -> FakeCollection:
        col = self.collections.get(name)
        if col is None:
            col = FakeCollection(name)
            self.collections[name] = col
        # Inject an error writing to the main collection (simulate a mid-swap failure).
        if name == self.addLoiTrenCollection:
            col.add = _raise_add  # type: ignore[assignment]
        return col

    def delete_collection(self, name: str) -> None:
        if name not in self.collections:
            raise ValueError(f"Collection '{name}' khong ton tai")
        del self.collections[name]


def _raise_add(*args, **kwargs):
    raise RuntimeError("Ghi vector that bai (gia lap)")


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
def fakeProvider():
    return FakeEmbeddingProvider()


@pytest.fixture()
def pipeline(session, fakeClient, fakeProvider):
    return DocumentPipeline(
        session,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=fakeProvider,
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


def _upload(pipeline, chu, kg, noiDung="Dong mot.\n\nDong hai.\n\nDong ba.") -> TaiLieu:
    pipeline.uploadDocument(chu, kg, noiDung.encode("utf-8"), "a.txt", "txt")
    return pipeline.db.query(TaiLieu).one()


# --- Successful commit embed ------------------------------------------------
def test_commit_chuyen_trang_thai_va_ghi_vector(pipeline, session, fakeClient, fakeProvider):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET

    ketQua = pipeline.commitEmbedding(chu, taiLieu.id)

    # Correct IndexingResult.
    assert ketQua.taiLieuId == taiLieu.id
    assert ketQua.trangThai == TrangThaiTaiLieu.DA_EMBED
    assert ketQua.soChunk == taiLieu.soChunk
    # State in the DB = DA_EMBED.
    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED
    # Vectors written into the workspace collection, correct chunk count + has taiLieuId.
    col = fakeClient.collections[kg.collectionName]
    assert col.count() == taiLieu.soChunk
    assert all(meta[META_TAI_LIEU_ID] == taiLieu.id for _, _, meta in col.rows.values())
    # The temp collection has been cleaned up.
    assert all("__tmp_" not in name for name in fakeClient.collections)
    assert fakeProvider.soLanGoi == 1


# --- Wrong state ------------------------------------------------------------
def test_commit_lan_hai_tu_choi_khi_da_embed(pipeline, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    pipeline.commitEmbedding(chu, taiLieu.id)

    # Committing again when already DA_EMBED -> ValidationError, state unchanged.
    with pytest.raises(ValidationError):
        pipeline.commitEmbedding(chu, taiLieu.id)
    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED


def test_commit_trang_thai_nap_bi_tu_choi(pipeline, session, fakeClient):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    taiLieu.trangThai = TrangThaiTaiLieu.NAP
    session.commit()

    with pytest.raises(ValidationError):
        pipeline.commitEmbedding(chu, taiLieu.id)
    # No vectors written.
    assert kg.collectionName not in fakeClient.collections


# --- Permission / existence -------------------------------------------------
def test_commit_khong_co_quyen_ghi_bi_tu_choi(pipeline, session, fakeClient):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.CHI_DOC))
    session.commit()

    with pytest.raises(AuthorizationError):
        pipeline.commitEmbedding(khach, taiLieu.id)
    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
    assert kg.collectionName not in fakeClient.collections


def test_commit_quyen_ghi_chia_se_thanh_cong(pipeline, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    taiLieu = _upload(pipeline, chu, kg)
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.GHI))
    session.commit()

    ketQua = pipeline.commitEmbedding(khach, taiLieu.id)
    assert ketQua.trangThai == TrangThaiTaiLieu.DA_EMBED


def test_commit_tai_lieu_khong_ton_tai(pipeline, session):
    chu = _tao_tai_khoan(session)
    _tao_khong_gian(session, chu)
    with pytest.raises(NotFoundError):
        pipeline.commitEmbedding(chu, "khong-ton-tai")


# --- Embedding error -> state unchanged, no vectors left behind ------------
def test_commit_embedding_loi_giu_nguyen_trang_thai(session, fakeClient):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    pipelineOk = DocumentPipeline(
        session,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=FakeEmbeddingProvider(),
    )
    taiLieu = _upload(pipelineOk, chu, kg)

    # Pipeline with an erroring provider.
    pipelineLoi = DocumentPipeline(
        session,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=FakeEmbeddingProvider(loi=True),
    )
    with pytest.raises(RuntimeError):
        pipelineLoi.commitEmbedding(chu, taiLieu.id)

    # State unchanged, NO vectors written (invariant R5.13).
    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
    assert kg.collectionName not in fakeClient.collections


def test_commit_loi_ghi_vector_khong_de_lai_vector_mot_phan(session, fakeClient):
    """Error writing into the main collection (after deleting the old one) -> clean up, no partial state."""
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    pipeline = DocumentPipeline(
        session,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=FakeEmbeddingProvider(),
    )
    taiLieu = _upload(pipeline, chu, kg)

    # Inject an error writing into the workspace's main collection.
    fakeClient.addLoiTrenCollection = kg.collectionName
    with pytest.raises(RuntimeError):
        pipeline.commitEmbedding(chu, taiLieu.id)

    # State unchanged; the main collection has no leftover partial vectors.
    session.refresh(taiLieu)
    assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
    col = fakeClient.collections.get(kg.collectionName)
    assert col is None or col.count() == 0
    # The temp collection has been cleaned up.
    assert all("__tmp_" not in name for name in fakeClient.collections)


# --- Resolve the provider for the workspace (not injected) -----------------
def test_commit_phan_giai_provider_theo_khong_gian(session, fakeClient, monkeypatch):
    """Not injecting embeddingProvider -> resolve the class via the registry for the workspace."""
    import app.pipelines.document_pipeline as dp

    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    pipelineUpload = DocumentPipeline(
        session,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=FakeEmbeddingProvider(),
    )
    taiLieu = _upload(pipelineUpload, chu, kg)

    # Make the registry return a fake class for the workspace's provider name.
    goiVoi = {}

    def _fake_get(ten):
        goiVoi["ten"] = ten
        return FakeEmbeddingProvider

    monkeypatch.setattr(dp, "get_embedding_provider", _fake_get)
    pipeline = DocumentPipeline(session, vectorStore=VectorStore(client=fakeClient))

    ketQua = pipeline.commitEmbedding(chu, taiLieu.id)
    assert ketQua.trangThai == TrangThaiTaiLieu.DA_EMBED
    assert goiVoi["ten"] == kg.embeddingProvider  # "huggingface"
