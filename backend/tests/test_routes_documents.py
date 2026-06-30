"""Tests for the document routes (task 13.3).

Uses a minimal FastAPI app containing the auth + workspaces + documents routers + the
global error handler. Overrides:
- `get_db` → a shared in-memory SQLite Session (no dependency on main.py).
- `get_document_pipeline` → a DocumentPipeline injected with a FAKE Embedding_Provider +
  a FAKE in-memory Vector_Store (reusing the fakes validated in unit test 8.4) — to avoid
  touching the real ChromaDB.

The auth router is used to register + log in for a token; the workspaces router to create
workspaces + shares. Covers the full flow:
- upload → preview (not yet embedded) → commit → DA_EMBED.
- paginated list; get chunks; edit chunks (PUT); rechunk; reset; delete.
- access control: an outsider → 404 for another person's workspace/document.

Validates: Requirements 5.1, 5.7, 5.8, 18.1, 18.3, 18.6, 18.7
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db, get_document_pipeline
from app.api.middleware.error_handler import register_error_handlers
from app.api.routes.auth import router as auth_router
from app.api.routes.documents import router as documents_router
from app.api.routes.workspaces import router as workspaces_router
from app.chunking.registry import discover_chunkers
from app.db.database import Base
from app.pipelines.document_pipeline import DocumentPipeline
from app.storage.vector_store import VectorStore

# Reuse the fake Embedding_Provider + fake Chroma client validated in task 8.4.
from tests.test_document_pipeline_commit import FakeClient, FakeEmbeddingProvider


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
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(workspaces_router)
    app.include_router(documents_router)

    fakeClient = FakeClient()
    fakeProvider = FakeEmbeddingProvider()

    def _override_get_db():
        yield session

    def _override_get_document_pipeline():
        return DocumentPipeline(
            session,
            vectorStore=VectorStore(client=fakeClient),
            embeddingProvider=fakeProvider,
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_document_pipeline] = _override_get_document_pipeline
    return TestClient(app)


# --- Helpers ---------------------------------------------------------------
def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_login(client, tenDangNhap: str) -> tuple[str, str]:
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


def _upload(client, token: str, wsId: str, noiDung: bytes, tenFile: str = "a.txt") -> dict:
    resp = client.post(
        f"/api/workspaces/{wsId}/documents",
        files={"file": (tenFile, noiDung, "text/plain")},
        data={"chienLuocChunk": "auto"},
        headers=_auth(token),
    )
    return resp


_VAN_BAN = "Dong mot.\n\nDong hai.\n\nDong ba.".encode("utf-8")


# --- Upload → preview → commit ---------------------------------------------
def test_upload_preview_roi_commit_chuyen_da_embed(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    # Upload → preview (not yet embedded).
    resp = _upload(client, token, wsId, _VAN_BAN)
    assert resp.status_code == 201, resp.text
    preview = resp.json()
    assert preview["soChunk"] >= 1
    assert len(preview["chunks"]) == preview["soChunk"]

    # Get the taiLieuId via the list.
    resp = client.get(f"/api/workspaces/{wsId}/documents", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    taiLieuId = resp.json()["items"][0]["id"]
    assert resp.json()["items"][0]["trangThai"] == "DA_PARSE_CHO_DUYET"

    # Commit → DA_EMBED.
    resp = client.post(f"/api/documents/{taiLieuId}/commit", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["taiLieuId"] == taiLieuId
    assert body["trangThai"] == "DA_EMBED"
    assert body["soChunk"] == preview["soChunk"]


def test_upload_dinh_dang_khong_ho_tro_tra_400(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    resp = _upload(client, token, wsId, b"noi dung", tenFile="a.docx")
    assert resp.status_code == 400, resp.text


# --- Paginated list -------------------------------------------------------
def test_list_documents_phan_trang(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    for i in range(3):
        assert _upload(client, token, wsId, _VAN_BAN, tenFile=f"d{i}.txt").status_code == 201

    resp = client.get(
        f"/api/workspaces/{wsId}/documents?page=1&pageSize=2", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["tongSo"] == 3
    assert body["page"] == 1
    assert body["pageSize"] == 2
    assert len(body["items"]) == 2


def test_list_documents_page_size_ngoai_khoang_tra_400(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    resp = client.get(
        f"/api/workspaces/{wsId}/documents?page=1&pageSize=0", headers=_auth(token)
    )
    assert resp.status_code == 400, resp.text


# --- Get / edit chunks -----------------------------------------------------
def test_get_chunks_va_edit_merge(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    _upload(client, token, wsId, _VAN_BAN)
    taiLieuId = client.get(
        f"/api/workspaces/{wsId}/documents", headers=_auth(token)
    ).json()["items"][0]["id"]

    # GET chunks.
    resp = client.get(f"/api/documents/{taiLieuId}/chunks", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    soChunkBanDau = resp.json()["soChunk"]

    if soChunkBanDau >= 2:
        # PUT merge the first two chunks → one fewer chunk.
        resp = client.put(
            f"/api/documents/{taiLieuId}/chunks",
            json=[{"loai": "merge", "viTri": 0}],
            headers=_auth(token),
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()) == soChunkBanDau - 1


def test_edit_chunks_vi_tri_khong_hop_le_tra_400(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    _upload(client, token, wsId, _VAN_BAN)
    taiLieuId = client.get(
        f"/api/workspaces/{wsId}/documents", headers=_auth(token)
    ).json()["items"][0]["id"]

    resp = client.put(
        f"/api/documents/{taiLieuId}/chunks",
        json=[{"loai": "merge", "viTri": 999}],
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text


# --- Rechunk / reset -------------------------------------------------------
def test_rechunk_va_reset(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    _upload(client, token, wsId, _VAN_BAN)
    taiLieuId = client.get(
        f"/api/workspaces/{wsId}/documents", headers=_auth(token)
    ).json()["items"][0]["id"]

    # Rechunk with new parameters (small size) — still >= 1 chunk.
    resp = client.post(
        f"/api/documents/{taiLieuId}/rechunk",
        json={"chienLuocChunk": "recursive", "thamSo": {"kichThuocMucTieu": 10, "doChongLan": 2}},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["soChunk"] >= 1

    # Reset to defaults + re-chunk.
    resp = client.post(f"/api/documents/{taiLieuId}/reset", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["soChunk"] >= 1


def test_rechunk_khong_body(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    _upload(client, token, wsId, _VAN_BAN)
    taiLieuId = client.get(
        f"/api/workspaces/{wsId}/documents", headers=_auth(token)
    ).json()["items"][0]["id"]

    # POST rechunk with no body → keep the current strategy/parameters.
    resp = client.post(f"/api/documents/{taiLieuId}/rechunk", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json()["soChunk"] >= 1


# --- Delete ----------------------------------------------------------------
def test_delete_document(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    _upload(client, token, wsId, _VAN_BAN)
    taiLieuId = client.get(
        f"/api/workspaces/{wsId}/documents", headers=_auth(token)
    ).json()["items"][0]["id"]

    resp = client.delete(f"/api/documents/{taiLieuId}", headers=_auth(token))
    assert resp.status_code == 204, resp.text

    # The list no longer contains the document.
    resp = client.get(f"/api/workspaces/{wsId}/documents", headers=_auth(token))
    assert resp.json()["tongSo"] == 0


def test_delete_document_khong_ton_tai_tra_404(client):
    token, _ = _register_login(client, "alice")
    resp = client.delete("/api/documents/khong-ton-tai", headers=_auth(token))
    assert resp.status_code == 404, resp.text


# --- Access control ----------------------------------------------------
def test_nguoi_ngoai_cuoc_khong_thay_khong_gian_tra_404(client):
    ownerToken, _ = _register_login(client, "owner")
    outsiderToken, _ = _register_login(client, "outsider")
    wsId = _create_workspace(client, ownerToken)
    _upload(client, ownerToken, wsId, _VAN_BAN)
    taiLieuId = client.get(
        f"/api/workspaces/{wsId}/documents", headers=_auth(ownerToken)
    ).json()["items"][0]["id"]

    # An outsider's list → 404 (does not reveal the workspace's existence).
    resp = client.get(
        f"/api/workspaces/{wsId}/documents", headers=_auth(outsiderToken)
    )
    assert resp.status_code == 404, resp.text

    # An outsider's upload → 404.
    resp = _upload(client, outsiderToken, wsId, _VAN_BAN)
    assert resp.status_code == 404, resp.text

    # Accessing another person's document → 403 (the document scope exists, the pipeline
    # checks WRITE permission on the workspace: the outsider lacks it → AuthorizationError).
    resp = client.get(
        f"/api/documents/{taiLieuId}/chunks", headers=_auth(outsiderToken)
    )
    assert resp.status_code == 403, resp.text


def test_chi_doc_khong_duoc_upload_tra_403(client):
    ownerToken, _ = _register_login(client, "owner")
    guestToken, guestId = _register_login(client, "guest")
    wsId = _create_workspace(client, ownerToken)

    # Share CHI_DOC with guest.
    resp = client.post(
        f"/api/workspaces/{wsId}/shares",
        json={"taiKhoanMucTieuId": guestId, "mucQuyen": "CHI_DOC"},
        headers=_auth(ownerToken),
    )
    assert resp.status_code == 201, resp.text

    # Guest can read the list (CHI_DOC) but cannot upload (needs GHI) → 403.
    assert (
        client.get(
            f"/api/workspaces/{wsId}/documents", headers=_auth(guestToken)
        ).status_code
        == 200
    )
    resp = _upload(client, guestToken, wsId, _VAN_BAN)
    assert resp.status_code == 403, resp.text
