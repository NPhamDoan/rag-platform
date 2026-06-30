"""Property test (task 8.2) — core invariant R5.13/R5.14.

Property 20: A vector exists if and only if the document is in the DA_EMBED state.

Idea: for ANY valid sequence of operations on a document (upload, then optionally
commit), at EVERY observation point the following invariant always holds:

    the workspace collection contains the document's vectors  <=>  trangThai == DA_EMBED

and at the same time:
    - DA_EMBED            -> vector count == soChunk (> 0)
    - DA_PARSE_CHO_DUYET  -> vector count == 0

Uses a FAKE Embedding_Provider + a FAKE/in-memory Vector_Store (injected via the
constructor) per the project convention. Each example uses its own in-memory SQLite +
fake vector store; the account/workspace are created directly (no bcrypt).
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import (
    HanMuc,
    KhongGianTaiLieu,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
)
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, VectorStore


# --- Fakes (replicated from test_document_pipeline_commit.py) ----------------
class FakeEmbeddingProvider:
    """Fake Embedding_Provider: a fixed vector based on text length (deterministic)."""

    ten = "fake"

    def embed(self, texts):
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
    """In-memory client: manages FakeCollections by name."""

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


# --- Setup ------------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _nap_chunker():
    discover_chunkers()


def _fresh_session():
    """Create a fresh in-memory SQLite + session (isolated for each Hypothesis example)."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, Session()


def _tao_tai_khoan(session) -> TaiKhoan:
    tk = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    tk.hanMuc = HanMuc()
    session.add(tk)
    session.commit()
    return tk


def _tao_khong_gian(session, chuSoHuu) -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten="KG",
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


def _dem_vector(fakeClient: FakeClient, collectionName: str, taiLieuId: str) -> int:
    """Count the vectors of a document in the workspace collection."""
    col = fakeClient.collections.get(collectionName)
    if col is None:
        return 0
    return sum(
        1 for (_, _, meta) in col.rows.values() if meta.get(META_TAI_LIEU_ID) == taiLieuId
    )


def _assert_invariant(taiLieu: TaiLieu, fakeClient: FakeClient, collectionName: str) -> None:
    """Invariant R5.13/R5.14: a vector exists  <=>  DA_EMBED (and the vector count is correct)."""
    soVector = _dem_vector(fakeClient, collectionName, taiLieu.id)
    if taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED:
        assert soVector == taiLieu.soChunk
        assert soVector > 0
    else:
        # DA_PARSE_CHO_DUYET (not yet committed) -> no vectors.
        assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
        assert soVector == 0


# --- Generators -------------------------------------------------------------
# A word made of letters/digits so the chunker produces meaningful chunks (>= 1 chunk).
_alphabet = st.characters(
    whitelist_categories=("Lu", "Ll", "Nd"),
    max_codepoint=0x024F,  # Basic + extended Latin (including accented characters)
)
_word = st.text(alphabet=_alphabet, min_size=1, max_size=12)
_paragraph = st.lists(_word, min_size=1, max_size=10).map(lambda ws: " ".join(ws))
# Text: multiple paragraphs, ensuring non-whitespace characters (>= 1 chunk when parsed).
_document_text = (
    st.lists(_paragraph, min_size=1, max_size=8)
    .map(lambda ps: "\n\n".join(ps))
    .filter(lambda s: s.strip() != "")
)


# Feature: multi-user-rag-platform, Property 20: A vector exists if and only if the
# document is in the DA_EMBED state (vector count == soChunk when DA_EMBED, == 0 when
# DA_PARSE_CHO_DUYET). Validates: Requirements 5.1, 5.13, 5.14
@settings(max_examples=100, deadline=None)
@given(noiDung=_document_text, coCommit=st.booleans())
def test_property_vector_iff_da_embed(noiDung: str, coCommit: bool) -> None:
    engine, session = _fresh_session()
    try:
        fakeClient = FakeClient()
        pipeline = DocumentPipeline(
            session,
            vectorStore=VectorStore(client=fakeClient),
            embeddingProvider=FakeEmbeddingProvider(),
        )
        chu = _tao_tai_khoan(session)
        kg = _tao_khong_gian(session, chu)

        # Operation 1: upload -> DA_PARSE_CHO_DUYET, NOT embedded, NO vectors.
        pipeline.uploadDocument(chu, kg, noiDung.encode("utf-8"), "a.txt", "txt")
        taiLieu = session.query(TaiLieu).one()
        assert taiLieu.trangThai == TrangThaiTaiLieu.DA_PARSE_CHO_DUYET
        _assert_invariant(taiLieu, fakeClient, kg.collectionName)

        # Operation 2 (optional): commit -> DA_EMBED, vectors = soChunk.
        if coCommit:
            pipeline.commitEmbedding(chu, taiLieu.id)
            session.refresh(taiLieu)
            assert taiLieu.trangThai == TrangThaiTaiLieu.DA_EMBED

        # Final observation point: the invariant always holds whether or not committed.
        _assert_invariant(taiLieu, fakeClient, kg.collectionName)
    finally:
        session.close()
        engine.dispose()
