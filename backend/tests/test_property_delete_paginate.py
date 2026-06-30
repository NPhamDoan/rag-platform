"""Property test (task 8.11) for DocumentPipeline: deleting documents + pagination.

# Feature: multi-user-rag-platform, Property 28: Deleting a document removes its Chunks;
# paginated listing is correct.
# Validates: Requirements 5.7, 5.8

A single Hypothesis test generates a sequence of upload/delete operations on one space
and checks two invariants:

(1) Pagination (R5.7): for a valid page/pageSize, `listDocuments` returns the correct
    slice — items per page = min(pageSize, max(0, N - (page-1)*pageSize)),
    `tongSo == N`, and the pages do NOT overlap, together covering all N documents
    exactly once (no duplicates, none missing).

(2) Deletion (R5.8): deleting a document removes itself + ALL its Chunks (and vectors
    from the fake Vector_Store) + its TomTat; `tongSo` decreases by exactly 1; the
    deleted document no longer appears on any page.

Uses a FAKE Embedding_Provider + FAKE in-memory Vector_Store; a fresh in-memory SQLite
for each example. Reuses the fakes from test_document_pipeline_manage.py.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import Chunk, HanMuc, KhongGianTaiLieu, TaiKhoan, TaiLieu, TomTatTaiLieu
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, VectorStore
from tests.test_document_pipeline_manage import FakeClient, FakeEmbeddingProvider

# Load the chunkers once for the whole module (self-registering registry).
discover_chunkers()

# Plain text (no "Dieu"/heading) -> recursive chunking, >=1 chunk, non-empty.
_NOI_DUNG = "Dong mot.\n\nDong hai.\n\nDong ba."


# --- Helper to build a fresh context for each example -----------------------
def _moiKhungCanh() -> tuple[DocumentPipeline, FakeClient, TaiKhoan, KhongGianTaiLieu, object, object]:
    """Create an in-memory SQLite engine/session + fake vector store + owner + space."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()

    chu = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    chu.hanMuc = HanMuc()
    db.add(chu)
    db.commit()

    kg = KhongGianTaiLieu(
        ten="KG",
        moTa="",
        chuSoHuuId=chu.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    db.add(kg)
    db.flush()
    kg.collectionName = f"ws_{kg.id}"
    db.commit()

    fakeClient = FakeClient()
    pipeline = DocumentPipeline(
        db,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=FakeEmbeddingProvider(),
    )
    return pipeline, fakeClient, chu, kg, db, engine


def _uploadLayId(pipeline: DocumentPipeline, chu, kg, idx: int) -> str:
    """Upload one document (unique file name) + commit embed, returning the new document id."""
    truoc = {
        t.id
        for t in pipeline.db.query(TaiLieu).filter(TaiLieu.khongGianId == kg.id).all()
    }
    pipeline.uploadDocument(chu, kg, _NOI_DUNG.encode("utf-8"), f"f{idx}.txt", "txt")
    sau = {
        t.id
        for t in pipeline.db.query(TaiLieu).filter(TaiLieu.khongGianId == kg.id).all()
    }
    taiLieuId = next(iter(sau - truoc))
    pipeline.commitEmbedding(chu, taiLieuId)  # create vectors in the fake store
    return taiLieuId


def _demVectorCuaDoc(fakeClient: FakeClient, kg, taiLieuId: str) -> int:
    """Count the vectors of one document in the space's fake collection."""
    col = fakeClient.collections.get(kg.collectionName)
    if col is None:
        return 0
    return sum(
        1 for (_, _, meta) in col.rows.values() if meta.get(META_TAI_LIEU_ID) == taiLieuId
    )


def _kiemPhanTrang(pipeline: DocumentPipeline, chu, kg, idsMongDoi: list[str], pageSize: int) -> None:
    """Check the pagination invariant: slice size, tongSo, no duplicates/missing."""
    N = len(idsMongDoi)
    soTrang = (N + pageSize - 1) // pageSize  # 0 when N == 0
    thuThap: list[str] = []
    for page in range(1, max(soTrang, 1) + 1):
        resp = pipeline.listDocuments(chu, kg.id, page=page, pageSize=pageSize)
        assert resp.tongSo == N
        assert resp.page == page
        assert resp.pageSize == pageSize
        kichThuocMongDoi = min(pageSize, max(0, N - (page - 1) * pageSize))
        assert len(resp.items) == kichThuocMongDoi
        thuThap.extend(d.id for d in resp.items)
    # Combine all pages: full coverage, exactly once (no duplicates, none missing).
    assert len(thuThap) == N
    assert len(set(thuThap)) == len(thuThap)  # no duplicates across pages
    assert set(thuThap) == set(idsMongDoi)  # full coverage + nothing missing


@settings(max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    lenhs=st.lists(st.sampled_from(["upload", "delete"]), min_size=1, max_size=16),
    boChon=st.lists(st.integers(min_value=0, max_value=9999), min_size=16, max_size=16),
    pageSize=st.integers(min_value=1, max_value=100),
)
def test_property_xoa_tai_lieu_va_phan_trang(lenhs, boChon, pageSize):
    pipeline, fakeClient, chu, kg, db, engine = _moiKhungCanh()
    try:
        idsHienTai: list[str] = []  # reference model of the currently existing ids
        soLanDel = 0

        for lenh in lenhs:
            if lenh == "upload":
                taiLieuId = _uploadLayId(pipeline, chu, kg, len(idsHienTai) + soLanDel)
                idsHienTai.append(taiLieuId)
            else:  # delete
                if not idsHienTai:
                    continue
                chon = boChon[soLanDel] % len(idsHienTai)
                soLanDel += 1
                docId = idsHienTai[chon]

                soChunkTruoc = (
                    db.query(Chunk).filter(Chunk.taiLieuId == docId).count()
                )
                assert soChunkTruoc > 0
                assert _demVectorCuaDoc(fakeClient, kg, docId) == soChunkTruoc
                tongTruoc = pipeline.listDocuments(chu, kg.id, page=1, pageSize=100).tongSo

                pipeline.deleteDocument(chu, docId)
                idsHienTai.remove(docId)

                # The document + its Chunks + TomTat are removed from the RDB.
                assert db.get(TaiLieu, docId) is None
                assert db.query(Chunk).filter(Chunk.taiLieuId == docId).count() == 0
                assert db.get(TomTatTaiLieu, docId) is None
                # The document's vectors are fully cleared from the fake store.
                assert _demVectorCuaDoc(fakeClient, kg, docId) == 0
                # tongSo decreases by exactly 1.
                tongSau = pipeline.listDocuments(chu, kg.id, page=1, pageSize=100).tongSo
                assert tongSau == tongTruoc - 1
                # The deleted document is no longer on any page.
                conLai = pipeline.listDocuments(chu, kg.id, page=1, pageSize=100)
                assert docId not in {d.id for d in conLai.items}

        # Final stable state: pagination invariant with a randomly generated pageSize.
        _kiemPhanTrang(pipeline, chu, kg, idsHienTai, pageSize)
    finally:
        db.close()
        engine.dispose()
