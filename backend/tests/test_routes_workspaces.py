"""Tests for the workspace + share + retrieval-config routes (task 13.2).

Uses a minimal FastAPI app containing the auth + workspaces routers + the global error
handler, overriding `get_db` to an in-memory SQLite Session (no dependency on main.py /
task 13.6). The auth router is used to register + log in for a token instead of using the
services directly.

Checks:
- create / list / update / delete a workspace (R3.1, R4.3-4, R4.6-8).
- grant share → the target account can read; revoke → loses access (404) (R11.1, R11.6).
- get / put the retrieval config (R19).
- isolation: an outsider account → 404 for another person's workspace (R3.2).

Validates: Requirements 3.1, 4.3, 4.5, 11.1, 11.6, 19.1
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db
from app.api.middleware.error_handler import register_error_handlers
from app.api.routes.auth import router as auth_router
from app.api.routes.workspaces import router as workspaces_router
from app.db.database import Base


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

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_login(client, tenDangNhap: str) -> tuple[str, str]:
    """Register + log in, returning (token, taiKhoanId)."""
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
    resp = client.post(
        "/api/workspaces", json={"ten": ten}, headers=_auth(token)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


# --- CRUD ------------------------------------------------------------------
def test_create_list_update_delete_workspace(client):
    token, _ = _register_login(client, "alice")

    # create
    resp = client.post(
        "/api/workspaces",
        json={"ten": "Du an luat", "moTa": "mo ta ban dau"},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    wsId = body["id"]
    assert body["ten"] == "Du an luat"
    assert body["moTa"] == "mo ta ban dau"
    assert body["collectionName"] == f"ws_{wsId}"

    # list
    resp = client.get("/api/workspaces", headers=_auth(token))
    assert resp.status_code == 200
    ids = [w["id"] for w in resp.json()]
    assert wsId in ids

    # update (rename + moTa)
    resp = client.patch(
        f"/api/workspaces/{wsId}",
        json={"ten": "Du an luat 2025", "moTa": "mo ta moi"},
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["ten"] == "Du an luat 2025"
    assert resp.json()["moTa"] == "mo ta moi"

    # delete
    resp = client.delete(f"/api/workspaces/{wsId}", headers=_auth(token))
    assert resp.status_code == 204

    resp = client.get("/api/workspaces", headers=_auth(token))
    assert wsId not in [w["id"] for w in resp.json()]


def test_create_workspace_ten_rong_tra_400(client):
    token, _ = _register_login(client, "alice")
    resp = client.post(
        "/api/workspaces", json={"ten": "   "}, headers=_auth(token)
    )
    assert resp.status_code == 400, resp.text


def test_list_workspaces_thieu_token_tra_401(client):
    assert client.get("/api/workspaces").status_code == 401


# --- Sharing ---------------------------------------------------------------
def test_grant_share_cho_phep_doc_revoke_thi_mat_quyen(client):
    ownerToken, _ = _register_login(client, "owner")
    guestToken, guestId = _register_login(client, "guest")
    wsId = _create_workspace(client, ownerToken)

    # Before sharing: guest does not see the workspace → 404 reading the config.
    resp = client.get(
        f"/api/workspaces/{wsId}/retrieval-config", headers=_auth(guestToken)
    )
    assert resp.status_code == 404

    # Owner shares CHI_DOC with guest.
    resp = client.post(
        f"/api/workspaces/{wsId}/shares",
        json={"taiKhoanMucTieuId": guestId, "mucQuyen": "CHI_DOC"},
        headers=_auth(ownerToken),
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["taiKhoanId"] == guestId

    # The workspace appears in the guest's list + the config is readable.
    resp = client.get("/api/workspaces", headers=_auth(guestToken))
    assert wsId in [w["id"] for w in resp.json()]
    resp = client.get(
        f"/api/workspaces/{wsId}/retrieval-config", headers=_auth(guestToken)
    )
    assert resp.status_code == 200, resp.text

    # Revoke → guest loses access → 404 again.
    resp = client.delete(
        f"/api/workspaces/{wsId}/shares/{guestId}", headers=_auth(ownerToken)
    )
    assert resp.status_code == 204
    resp = client.get(
        f"/api/workspaces/{wsId}/retrieval-config", headers=_auth(guestToken)
    )
    assert resp.status_code == 404


def test_chi_doc_khong_duoc_sua_cau_hinh_tra_403(client):
    ownerToken, _ = _register_login(client, "owner")
    guestToken, guestId = _register_login(client, "guest")
    wsId = _create_workspace(client, ownerToken)

    client.post(
        f"/api/workspaces/{wsId}/shares",
        json={"taiKhoanMucTieuId": guestId, "mucQuyen": "CHI_DOC"},
        headers=_auth(ownerToken),
    )

    # Guest has only CHI_DOC → PUT config is 403.
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


def test_non_owner_khong_duoc_chia_se_tra_403(client):
    ownerToken, _ = _register_login(client, "owner")
    _, strangerId = _register_login(client, "stranger")
    strangerToken, _ = _register_login(client, "stranger2")
    wsId = _create_workspace(client, ownerToken)

    # stranger2 is not the owner → ShareService rejects sharing with 403
    # (the service checks ownership of the already-existing workspace).
    resp = client.post(
        f"/api/workspaces/{wsId}/shares",
        json={"taiKhoanMucTieuId": strangerId, "mucQuyen": "CHI_DOC"},
        headers=_auth(strangerToken),
    )
    assert resp.status_code == 403, resp.text


# --- Retrieval config ----------------------------------------------------
def test_get_va_put_retrieval_config(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    # GET defaults (0.3 / 0.5 / k=8 / 0.5 / 0.5).
    resp = client.get(
        f"/api/workspaces/{wsId}/retrieval-config", headers=_auth(token)
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["k"] == 8
    assert body["khongGianId"] == wsId

    # PUT update.
    resp = client.put(
        f"/api/workspaces/{wsId}/retrieval-config",
        json={
            "nguongKhongTimThay": 0.25,
            "nguongDuLienQuan": 0.7,
            "k": 10,
            "trongSoVector": 0.6,
            "trongSoBm25": 0.4,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["k"] == 10

    # GET again reflects the new value.
    resp = client.get(
        f"/api/workspaces/{wsId}/retrieval-config", headers=_auth(token)
    )
    assert resp.json()["k"] == 10
    assert resp.json()["nguongDuLienQuan"] == 0.7


def test_put_retrieval_config_nguong_nguoc_tra_400(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    # nguongKhongTimThay > nguongDuLienQuan → DTO rejects it (400).
    resp = client.put(
        f"/api/workspaces/{wsId}/retrieval-config",
        json={
            "nguongKhongTimThay": 0.8,
            "nguongDuLienQuan": 0.3,
            "k": 8,
            "trongSoVector": 0.5,
            "trongSoBm25": 0.5,
        },
        headers=_auth(token),
    )
    assert resp.status_code == 400, resp.text


# --- Isolation ----------------------------------------------------------------
def test_co_lap_nguoi_ngoai_cuoc_khong_thay_khong_gian(client):
    ownerToken, _ = _register_login(client, "owner")
    outsiderToken, _ = _register_login(client, "outsider")
    wsId = _create_workspace(client, ownerToken)

    # Reading the config via require_workspace_access: the outsider has access level NONE
    # → 404 (does not reveal another person's workspace existence).
    assert (
        client.get(
            f"/api/workspaces/{wsId}/retrieval-config",
            headers=_auth(outsiderToken),
        ).status_code
        == 404
    )
    # DELETE workspace, since WorkspaceService checks ownership: the workspace exists but
    # the outsider is not the owner → 403.
    assert (
        client.delete(
            f"/api/workspaces/{wsId}", headers=_auth(outsiderToken)
        ).status_code
        == 403
    )
    # The outsider's list does not contain the owner's workspace.
    resp = client.get("/api/workspaces", headers=_auth(outsiderToken))
    assert wsId not in [w["id"] for w in resp.json()]
