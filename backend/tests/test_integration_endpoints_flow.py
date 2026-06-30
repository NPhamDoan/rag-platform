"""Integration test for the main endpoints (task 13.7).

Assembles ONE FastAPI app with ALL the routers (auth + workspaces + documents +
query + history + global error handler) over a single shared in-memory SQLite
Session, then runs the FULL real HTTP flow (TestClient):

    register -> login -> create workspace -> upload -> commit -> query -> history

Unlike the individual route tests (13.2-13.4) that only inject fixed results, this test
uses ONE SHARED in-memory Vector_Store between the DocumentPipeline and QueryPipeline: a
document is actually embedded into the store after `commit`, and the `query` step
retrieves that very chunk (true E2E). Only the synthesis/verification LLM is fake (to
avoid network calls).

Checks the status codes for data isolation:
- 401: request missing a token (R2.6).
- 404: an outsider does not see another person's workspace (R3.2 — does not disclose
  existence).
- 403: a CHI_DOC account cannot write (upload/commit/edit config) (R3.3).
- Each person's history is completely separate (R3.2/R3.3).

Validates: Requirements 2.6, 3.2, 3.3, 6.2
"""

from __future__ import annotations

import math

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import (
    get_db,
    get_document_pipeline,
    get_query_pipeline,
)
from app.api.middleware.error_handler import register_error_handlers
from app.api.middleware.rate_limit import get_rate_limiter
from app.api.routes.auth import router as auth_router
from app.api.routes.documents import router as documents_router
from app.api.routes.history import router as history_router
from app.api.routes.query import router as query_router
from app.api.routes.workspaces import router as workspaces_router
from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.db.models import NhanXacMinh
from app.pipelines.document_pipeline import DocumentPipeline
from app.pipelines.query_pipeline import QueryPipeline
from app.storage.vector_store import VectorStore


# --- Fakes: in-memory Vector_Store (supports BOTH commit and hybrid search) -
class _InMemoryCollection:
    """In-memory collection: add/delete(where+ids)/count/get/query (matching ChromaDB).

    `query` ranks by Euclidean distance to the question vector (nearest first) to
    mimic ChromaDB's vector ranking; `get` returns every record for BM25 + returns the
    text/metadata with the results.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.rows: dict[str, tuple] = {}  # id -> (vector, document, metadata)

    def add(self, *, ids, embeddings, documents, metadatas) -> None:
        for i, vec, doc, meta in zip(ids, embeddings, documents, metadatas):
            self.rows[i] = (list(vec), doc, dict(meta))

    def delete(self, *, where=None, ids=None) -> None:
        if ids is not None:
            for i in ids:
                self.rows.pop(i, None)
        if where:
            victims = [
                i
                for i, (_, _, meta) in self.rows.items()
                if all(meta.get(k) == v for k, v in where.items())
            ]
            for i in victims:
                self.rows.pop(i, None)

    def count(self) -> int:
        return len(self.rows)

    def get(self) -> dict:
        ids = list(self.rows.keys())
        return {
            "ids": ids,
            "documents": [self.rows[i][1] for i in ids],
            "metadatas": [self.rows[i][2] for i in ids],
        }

    def query(self, *, query_embeddings, n_results) -> dict:
        qv = query_embeddings[0]

        def khoangCach(vec):
            return math.sqrt(sum((a - b) ** 2 for a, b in zip(vec, qv)))

        xepHang = sorted(self.rows.items(), key=lambda kv: (khoangCach(kv[1][0]), kv[0]))
        return {"ids": [[i for i, _ in xepHang[:n_results]]]}


class _InMemoryClient:
    """In-memory client: manages _InMemoryCollection instances by name."""

    def __init__(self) -> None:
        self.collections: dict[str, _InMemoryCollection] = {}

    def get_or_create_collection(self, name: str) -> _InMemoryCollection:
        col = self.collections.get(name)
        if col is None:
            col = _InMemoryCollection(name)
            self.collections[name] = col
        return col

    def delete_collection(self, name: str) -> None:
        if name not in self.collections:
            raise ValueError(f"Collection '{name}' khong ton tai")
        del self.collections[name]


class _FakeEmbeddingProvider:
    """Fake Embedding_Provider: a deterministic vector based on text length ([len, 1, 0])."""

    ten = "fake"

    def __init__(self) -> None:
        self.soLanGoi = 0

    def embed(self, texts):
        self.soLanGoi += 1
        return [[float(len(t)), 1.0, 0.0] for t in texts]


class _FakeLLM:
    """Fake LLM_Provider: returns a fixed `phanHoi` (no network call)."""

    ten = "fake-llm"

    def __init__(self, phanHoi: str) -> None:
        self.phanHoi = phanHoi

    def generate(self, systemPrompt: str, userPrompt: str) -> str:
        return self.phanHoi


# The synthesized answer has marker [1] -> maps to exactly ONE TrichDan (the first chunk).
_TRA_LOI_TONG_HOP = "Theo tai lieu, toc do toi da trong khu dan cu la 60 km/h [1]."

# Single-chunk text (no blank lines) -> commit produces exactly 1 chunk; the query shares
# keywords so that chunk ranks #1 in both vector and BM25 -> RRF score = 1.0 >= the 0.5 threshold.
_VAN_BAN = "Toc do toi da cho phep trong khu dan cu la sau muoi km mot gio.".encode(
    "utf-8"
)
_CAU_HOI = "Toc do toi da trong khu dan cu la bao nhieu?"


# --- Fixtures ---------------------------------------------------------------
@pytest.fixture(scope="module", autouse=True)
def _nap_chunker():
    """Load the chunkers (self-registering registry) once for the whole module."""
    discover_chunkers()


@pytest.fixture()
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def client(session):
    # The rate limiter's state lives for the process lifetime -> reset for each test.
    get_rate_limiter().reset()

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(workspaces_router)
    app.include_router(documents_router)
    app.include_router(query_router)
    app.include_router(history_router)

    # ONE SHARED in-memory vector client: commit writes into it -> query reads it back.
    sharedClient = _InMemoryClient()
    embeddingProvider = _FakeEmbeddingProvider()

    def _override_get_db():
        yield session

    def _override_get_document_pipeline():
        return DocumentPipeline(
            session,
            vectorStore=VectorStore(client=sharedClient),
            embeddingProvider=embeddingProvider,
        )

    def _override_get_query_pipeline():
        return QueryPipeline(
            session,
            synthesisProvider=_FakeLLM(_TRA_LOI_TONG_HOP),
            verifyProvider=_FakeLLM("da xac minh"),
            embeddingProvider=embeddingProvider,
            vectorStore=VectorStore(client=sharedClient),
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_document_pipeline] = _override_get_document_pipeline
    app.dependency_overrides[get_query_pipeline] = _override_get_query_pipeline
    return TestClient(app)


# --- Helpers ---------------------------------------------------------------
def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_login(client, tenDangNhap: str) -> tuple[str, str]:
    """Register + login; returns (token, taiKhoanId)."""
    resp = client.post(
        "/api/auth/register",
        json={
            "email": f"{tenDangNhap}@example.com",
            "tenDangNhap": tenDangNhap,
            "matKhau": "matkhau123",
        },
    )
    assert resp.status_code == 201, resp.text
    taiKhoanId = resp.json()["id"]

    resp = client.post(
        "/api/auth/login",
        json={"tenDangNhap": tenDangNhap, "matKhau": "matkhau123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"], taiKhoanId


def _create_workspace(client, token: str, ten: str = "Khong gian A") -> str:
    resp = client.post("/api/workspaces", json={"ten": ten}, headers=_auth(token))
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _upload(client, token: str, wsId: str, noiDung: bytes = _VAN_BAN, tenFile: str = "luat.txt"):
    return client.post(
        f"/api/workspaces/{wsId}/documents",
        files={"file": (tenFile, noiDung, "text/plain")},
        data={"chienLuocChunk": "auto"},
        headers=_auth(token),
    )


def _first_document_id(client, token: str, wsId: str) -> str:
    resp = client.get(f"/api/workspaces/{wsId}/documents", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["items"][0]["id"]


def _share(client, token: str, wsId: str, taiKhoanMucTieuId: str, mucQuyen: str) -> None:
    resp = client.post(
        f"/api/workspaces/{wsId}/shares",
        json={"taiKhoanMucTieuId": taiKhoanMucTieuId, "mucQuyen": mucQuyen},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text


# --- Full flow: register -> ... -> history (R2.6, R6.2) --------------------
def test_luong_day_du_tu_dang_ky_den_lich_su(client):
    # 1) Register + login (R2.6: only a valid token can access).
    token, _ = _register_login(client, "alice")

    # 2) Create a workspace.
    wsId = _create_workspace(client, token, "Khong gian luat")

    # 3) Upload -> preview (not yet embedded).
    resp = _upload(client, token, wsId)
    assert resp.status_code == 201, resp.text
    preview = resp.json()
    assert preview["soChunk"] == 1  # single-chunk text
    taiLieuId = _first_document_id(client, token, wsId)

    # 4) Commit -> DA_EMBED (vectors written into the shared store).
    resp = client.post(f"/api/documents/{taiLieuId}/commit", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["trangThai"] == "DA_EMBED"

    # 5) Query: retrieve the very chunk just embedded -> synthesize an answer + TrichDan (R6.2).
    resp = client.post(
        f"/api/workspaces/{wsId}/query",
        json={"cauHoi": _CAU_HOI},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["traLoi"] == _TRA_LOI_TONG_HOP
    assert body["laFallback"] is False
    assert body["nhanXacMinh"] == NhanXacMinh.DA_XAC_MINH.value
    # Marker [1] -> exactly ONE TrichDan, pointing to the chunk of the document just committed.
    assert len(body["trichDan"]) == 1
    assert body["trichDan"][0]["marker"] == 1
    assert body["trichDan"][0]["taiLieuId"] == taiLieuId

    # 6) History: the query turn has been saved (R6.2/R9).
    resp = client.get(f"/api/workspaces/{wsId}/history", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    muc = resp.json()
    assert len(muc) == 1
    assert muc[0]["cauHoi"] == _CAU_HOI
    assert muc[0]["traLoi"] == _TRA_LOI_TONG_HOP


# --- Isolation: 401 missing token (R2.6) -----------------------------------
def test_thieu_token_tra_401(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    assert client.get("/api/workspaces").status_code == 401
    assert (
        client.post(f"/api/workspaces/{wsId}/query", json={"cauHoi": _CAU_HOI}).status_code
        == 401
    )
    assert client.get(f"/api/workspaces/{wsId}/history").status_code == 401
    assert client.get(f"/api/workspaces/{wsId}/documents").status_code == 401


# --- Isolation: 404 for an outsider (R3.2) ---------------------------------
def test_nguoi_ngoai_cuoc_tra_404(client):
    ownerToken, _ = _register_login(client, "owner")
    outsiderToken, _ = _register_login(client, "outsider")
    wsId = _create_workspace(client, ownerToken)
    assert _upload(client, ownerToken, wsId).status_code == 201

    # No share at all -> the outsider does not see the workspace (does not disclose existence).
    assert (
        client.post(
            f"/api/workspaces/{wsId}/query",
            json={"cauHoi": _CAU_HOI},
            headers=_auth(outsiderToken),
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/workspaces/{wsId}/history", headers=_auth(outsiderToken)
        ).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/workspaces/{wsId}/documents", headers=_auth(outsiderToken)
        ).status_code
        == 404
    )
    # The outsider's list does not contain the owner's workspace.
    resp = client.get("/api/workspaces", headers=_auth(outsiderToken))
    assert wsId not in [w["id"] for w in resp.json()]


# --- Isolation: 403 CHI_DOC cannot write (R3.3) ----------------------------
def test_chi_doc_khong_duoc_ghi_tra_403(client):
    ownerToken, _ = _register_login(client, "owner")
    guestToken, guestId = _register_login(client, "guest")
    wsId = _create_workspace(client, ownerToken)
    assert _upload(client, ownerToken, wsId).status_code == 201
    taiLieuId = _first_document_id(client, ownerToken, wsId)

    # Owner shares CHI_DOC with the guest.
    _share(client, ownerToken, wsId, guestId, "CHI_DOC")

    # The guest can READ (document list) but cannot WRITE.
    assert (
        client.get(
            f"/api/workspaces/{wsId}/documents", headers=_auth(guestToken)
        ).status_code
        == 200
    )
    # Upload (needs GHI) -> 403.
    assert _upload(client, guestToken, wsId).status_code == 403
    # Commit the owner's document (needs GHI) -> 403.
    assert (
        client.post(
            f"/api/documents/{taiLieuId}/commit", headers=_auth(guestToken)
        ).status_code
        == 403
    )
    # Edit the retrieval config (needs GHI) -> 403.
    resp = client.put(
        f"/api/workspaces/{wsId}/retrieval-config",
        json={
            "nguongKhongTimThay": 0.2,
            "nguongDuLienQuan": 0.6,
            "k": 6,
            "trongSoVector": 0.5,
            "trongSoBm25": 0.5,
        },
        headers=_auth(guestToken),
    )
    assert resp.status_code == 403, resp.text


# --- History isolation between two people (R3.2/R3.3) ----------------------
def test_lich_su_co_lap_giua_hai_nguoi(client):
    ownerToken, _ = _register_login(client, "owner")
    guestToken, guestId = _register_login(client, "guest")
    wsId = _create_workspace(client, ownerToken)

    # Owner uploads + commits so the query can retrieve.
    assert _upload(client, ownerToken, wsId).status_code == 201
    taiLieuId = _first_document_id(client, ownerToken, wsId)
    assert (
        client.post(f"/api/documents/{taiLieuId}/commit", headers=_auth(ownerToken)).status_code
        == 200
    )

    # Share CHI_DOC with the guest (the guest can read/query).
    _share(client, ownerToken, wsId, guestId, "CHI_DOC")

    # Owner queries twice, guest once in the SAME workspace.
    for _ in range(2):
        assert (
            client.post(
                f"/api/workspaces/{wsId}/query",
                json={"cauHoi": _CAU_HOI},
                headers=_auth(ownerToken),
            ).status_code
            == 200
        )
    assert (
        client.post(
            f"/api/workspaces/{wsId}/query",
            json={"cauHoi": _CAU_HOI},
            headers=_auth(guestToken),
        ).status_code
        == 200
    )

    owner_muc = client.get(
        f"/api/workspaces/{wsId}/history", headers=_auth(ownerToken)
    ).json()
    guest_muc = client.get(
        f"/api/workspaces/{wsId}/history", headers=_auth(guestToken)
    ).json()
    # Each person sees only THEIR OWN history.
    assert len(owner_muc) == 2
    assert len(guest_muc) == 1
    owner_ids = {m["id"] for m in owner_muc}
    assert guest_muc[0]["id"] not in owner_ids
