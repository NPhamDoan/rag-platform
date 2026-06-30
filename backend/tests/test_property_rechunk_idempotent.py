"""Property-based test (task 8.6) for DocumentPipeline.rechunk + commitEmbedding.

# Feature: multi-user-rag-platform, Property 23: Re-chunk / re-embed is idempotent
and replace-set. For any document text + chunking strategy/params, re-chunking
twice (same params) yields exactly ONE set of Chunks (same thuTu/viTri/noiDung),
with no old Chunks left over (replace-set — new chunk ids are disjoint from old ids,
total count == soChunk), and re-embedding (commit -> rechunk -> commit) is
consistent: the Vector_Store reflects EXACTLY the current chunk set after each
commit (R21.4).

Reuses the FAKES from tests/test_document_pipeline_rechunk.py (FakeEmbeddingProvider,
FakeClient/FakeCollection) + VectorStore(client=fakeClient) per the project
convention. Each example uses its own in-memory SQLite + fake store; TaiKhoan/
KhongGian are created directly.

**Validates: Requirements 5.12, 18.2, 18.6, 18.7, 21.4**
"""

from __future__ import annotations

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.base import ChunkParams
from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import (
    Chunk,
    HanMuc,
    KhongGianTaiLieu,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
)
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import VectorStore

# Reuse the fakes already validated in unit test 8.5.
from tests.test_document_pipeline_rechunk import (
    FakeClient,
    FakeEmbeddingProvider,
)

# Load the chunking strategy registry once (the @register_chunker decorator runs on import).
discover_chunkers()


# --- Generators -------------------------------------------------------------
# Alphabet: letters + digits + spaces + newlines + punctuation to create rich
# paragraph/line/sentence boundaries for the chunking strategies.
_ALPHABET = "abcdefghijk ABCDEFG 0123.,\n"

_text_strategy = st.text(alphabet=_ALPHABET, min_size=1, max_size=500)

_strategy_strategy = st.sampled_from(
    ["auto", "recursive", "structure-aware", "semantic", "page"]
)


@st.composite
def _chunk_params(draw) -> ChunkParams:
    """Generate valid ChunkParams (doChongLan < kichThuocMucTieu)."""
    kichThuoc = draw(st.integers(min_value=5, max_value=300))
    doChongLan = draw(st.integers(min_value=0, max_value=kichThuoc - 1))
    return ChunkParams(kichThuocMucTieu=kichThuoc, doChongLan=doChongLan)


# --- Helpers ----------------------------------------------------------------
def _fingerprint(chunks):
    """Fingerprint (thuTu, batDau, ketThuc, noiDung) — ignoring id (id differs each time)."""
    return [(c.thuTu, c.viTriBatDau, c.viTriKetThuc, c.noiDung) for c in chunks]


def _store_ids(fakeClient, collectionName) -> set[str]:
    """Set of vector ids currently in the workspace collection (empty if not created)."""
    col = fakeClient.collections.get(collectionName)
    return set(col.rows.keys()) if col is not None else set()


def _chunk_ids(session, taiLieuId) -> set[str]:
    return {c.id for c in session.query(Chunk).filter(Chunk.taiLieuId == taiLieuId).all()}


def _make_env():
    """Create a clean environment: in-memory SQLite + fake store + pipeline + owner/workspace."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = Session()

    fakeClient = FakeClient()
    pipeline = DocumentPipeline(
        session,
        vectorStore=VectorStore(client=fakeClient),
        embeddingProvider=FakeEmbeddingProvider(),
    )

    chu = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    chu.hanMuc = HanMuc()
    session.add(chu)
    session.commit()

    kg = KhongGianTaiLieu(
        ten="KG",
        moTa="",
        chuSoHuuId=chu.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    session.add(kg)
    session.flush()
    kg.collectionName = f"ws_{kg.id}"
    session.commit()
    return engine, session, fakeClient, pipeline, chu, kg


# --- Property 23 ------------------------------------------------------------
@settings(max_examples=90, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(text=_text_strategy, chienLuoc=_strategy_strategy, thamSo=_chunk_params())
def test_rechunk_va_embed_lai_idempotent_thay_sach(text, chienLuoc, thamSo):
    # Text must have content (not whitespace only) to create >= 1 chunk (R15.2).
    assume(text.strip())

    engine, session, fakeClient, pipeline, chu, kg = _make_env()
    try:
        # Upload (auto, default params) -> DA_PARSE_CHO_DUYET, not yet embedded.
        pipeline.uploadDocument(chu, kg, text.encode("utf-8"), "a.txt", "txt")
        taiLieu = session.query(TaiLieu).one()
        idChunkCu = _chunk_ids(session, taiLieu.id)

        # Re-chunk twice with the SAME strategy/params.
        lan1 = pipeline.rechunk(chu, taiLieu.id, chienLuocChunk=chienLuoc, thamSo=thamSo)
        idSauLan1 = _chunk_ids(session, taiLieu.id)
        lan2 = pipeline.rechunk(chu, taiLieu.id, chienLuocChunk=chienLuoc, thamSo=thamSo)
        idSauLan2 = _chunk_ids(session, taiLieu.id)

        # (a) Idempotent: same noiDung/viTri/thuTu across the two re-chunks.
        assert _fingerprint(lan1.chunks) == _fingerprint(lan2.chunks)

        # (b) Replace-set: new chunk ids are disjoint from old chunk ids (upload +
        # previous run), and the total chunk count in the RDB == reported soChunk.
        assert idSauLan1.isdisjoint(idChunkCu)
        assert idSauLan2.isdisjoint(idChunkCu)
        assert idSauLan2.isdisjoint(idSauLan1)
        assert session.query(Chunk).filter(Chunk.taiLieuId == taiLieu.id).count() == lan2.soChunk
        session.refresh(taiLieu)
        assert taiLieu.soChunk == lan2.soChunk

        # (c) Consistent re-embedding (commit -> rechunk -> commit), R21.4:
        # Commit 1: the Vector_Store reflects EXACTLY the current chunk set.
        pipeline.commitEmbedding(chu, taiLieu.id)
        session.refresh(taiLieu)
        assert taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED
        assert _store_ids(fakeClient, kg.collectionName) == _chunk_ids(session, taiLieu.id)

        # Re-chunk -> invalidates the embedding: back to DA_PARSE_CHO_DUYET + clears vectors.
        lan3 = pipeline.rechunk(chu, taiLieu.id, chienLuocChunk=chienLuoc, thamSo=thamSo)
        session.refresh(taiLieu)
        assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
        assert _store_ids(fakeClient, kg.collectionName) == set()
        # Still idempotent after having been embedded.
        assert _fingerprint(lan3.chunks) == _fingerprint(lan2.chunks)

        # Commit 2: re-embed -> the store again reflects EXACTLY the current chunk set.
        pipeline.commitEmbedding(chu, taiLieu.id)
        session.refresh(taiLieu)
        assert taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED
        assert _store_ids(fakeClient, kg.collectionName) == _chunk_ids(session, taiLieu.id)
    finally:
        session.close()
        engine.dispose()
