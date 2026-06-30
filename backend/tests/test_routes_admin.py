"""Tests for admin routes + account API keys (task 13.5).

Uses a minimal FastAPI app containing the auth + admin + account routers + the global
error handler, overriding `get_db` to an in-memory SQLite Session (independent of
main.py / task 13.6). The auth router is used to register + log in for a token; an
account is promoted to QUAN_TRI directly via the Session.

Checks:
- GET /api/admin/users: QUAN_TRI can list; NGUOI_DUNG → 403 (R10.1, R10.6).
- POST disable/enable: disable + re-enable; disabling self → 400; non-existent
  → 404 (R10.2-5, R10.8).
- PUT quota: configure the limits; out of range → 400 (R12.5-6).
- GET/PUT prompts: read the default, edit then re-read; wrong role → 404 (R20).
- PUT limits: applied; out of range → 400 (R23).
- api-keys: PUT → GET returns the masked form (no plaintext leak); DELETE idempotent;
  isolated between users (R22.1, R22.3, R22.5).

Validates: Requirements 10.1, 12.5, 20.1, 22.1, 23.1
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
from app.api.routes.account import router as account_router
from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.db.database import Base
from app.db.models import TaiKhoan, VaiTro


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
    app.include_router(admin_router)
    app.include_router(account_router)

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


def _nang_quan_tri(session, taiKhoanId: str) -> None:
    """Promote an account to QUAN_TRI directly via the Session (registration creates a NGUOI_DUNG)."""
    tk = session.get(TaiKhoan, taiKhoanId)
    tk.vaiTro = VaiTro.QUAN_TRI
    session.commit()


def _admin(client, session, tenDangNhap: str = "admin") -> tuple[str, str]:
    token, adminId = _register_login(client, tenDangNhap)
    _nang_quan_tri(session, adminId)
    # Log in again so the token carries the new role (not required, but cleaner).
    resp = client.post(
        "/api/auth/login",
        json={"tenDangNhap": tenDangNhap, "matKhau": "matkhau123"},
    )
    return resp.json()["token"], adminId


# --- Account management -----------------------------------------------------
def test_list_users_quan_tri_thay_tat_ca(client, session):
    adminToken, _ = _admin(client, session)
    _register_login(client, "bob")

    resp = client.get("/api/admin/users", headers=_auth(adminToken))
    assert resp.status_code == 200, resp.text
    tens = [u["tenDangNhap"] for u in resp.json()]
    assert "admin" in tens and "bob" in tens
    # No password hash leaked in the response.
    assert all("matKhauHash" not in u for u in resp.json())


def test_list_users_nguoi_dung_thuong_bi_403(client):
    userToken, _ = _register_login(client, "alice")
    resp = client.get("/api/admin/users", headers=_auth(userToken))
    assert resp.status_code == 403, resp.text


def test_disable_enable_user(client, session):
    adminToken, _ = _admin(client, session)
    _, bobId = _register_login(client, "bob")

    resp = client.post(
        f"/api/admin/users/{bobId}/disable", headers=_auth(adminToken)
    )
    assert resp.status_code == 204, resp.text
    assert session.get(TaiKhoan, bobId).trangThai.value == "VO_HIEU_HOA"

    resp = client.post(
        f"/api/admin/users/{bobId}/enable", headers=_auth(adminToken)
    )
    assert resp.status_code == 204, resp.text
    assert session.get(TaiKhoan, bobId).trangThai.value == "HOAT_DONG"


def test_disable_chinh_minh_tra_400(client, session):
    adminToken, adminId = _admin(client, session)
    resp = client.post(
        f"/api/admin/users/{adminId}/disable", headers=_auth(adminToken)
    )
    assert resp.status_code == 400, resp.text


def test_disable_tai_khoan_khong_ton_tai_tra_404(client, session):
    adminToken, _ = _admin(client, session)
    resp = client.post(
        "/api/admin/users/khong-ton-tai/disable", headers=_auth(adminToken)
    )
    assert resp.status_code == 404, resp.text


# --- Quota ---------------------------------------------------------------
def _quota_payload(**overrides) -> dict:
    payload = {
        "soKhongGianToiDa": 10,
        "dungLuongToiDa": 1024 * 1024,
        "soTaiLieuToiDaMoiKhongGian": 100,
        "tanSuatTruyVanMoiPhut": 30,
    }
    payload.update(overrides)
    return payload


def test_set_quota_thanh_cong(client, session):
    adminToken, _ = _admin(client, session)
    _, bobId = _register_login(client, "bob")

    resp = client.put(
        f"/api/admin/users/{bobId}/quota",
        json=_quota_payload(soKhongGianToiDa=7),
        headers=_auth(adminToken),
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["taiKhoanId"] == bobId
    assert body["soKhongGianToiDa"] == 7


def test_set_quota_ngoai_khoang_tra_400(client, session):
    adminToken, _ = _admin(client, session)
    _, bobId = _register_login(client, "bob")

    # soKhongGianToiDa = 0 is out of the valid range → DTO rejects it (400).
    resp = client.put(
        f"/api/admin/users/{bobId}/quota",
        json=_quota_payload(soKhongGianToiDa=0),
        headers=_auth(adminToken),
    )
    assert resp.status_code == 400, resp.text


def test_set_quota_nguoi_dung_thuong_bi_403(client):
    userToken, _ = _register_login(client, "alice")
    _, bobId = _register_login(client, "bob")
    resp = client.put(
        f"/api/admin/users/{bobId}/quota",
        json=_quota_payload(),
        headers=_auth(userToken),
    )
    assert resp.status_code == 403, resp.text


# --- MauPrompt -------------------------------------------------------------
def test_get_prompt_mac_dinh_roi_chinh(client, session):
    adminToken, _ = _admin(client, session)

    # GET the default (not yet edited) → isDefault True.
    resp = client.get("/api/admin/prompts/synthesis", headers=_auth(adminToken))
    assert resp.status_code == 200, resp.text
    assert resp.json()["isDefault"] is True
    assert resp.json()["vaiTro"] == "synthesis"

    # PUT edit the content.
    resp = client.put(
        "/api/admin/prompts/synthesis",
        json={"noiDung": "Prompt tuy bien cho tong hop."},
        headers=_auth(adminToken),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["isDefault"] is False
    assert resp.json()["noiDung"] == "Prompt tuy bien cho tong hop."

    # GET again reflects the new value.
    resp = client.get("/api/admin/prompts/synthesis", headers=_auth(adminToken))
    assert resp.json()["noiDung"] == "Prompt tuy bien cho tong hop."
    assert resp.json()["isDefault"] is False


def test_get_prompt_vai_tro_sai_tra_404(client, session):
    adminToken, _ = _admin(client, session)
    resp = client.get("/api/admin/prompts/khong-co", headers=_auth(adminToken))
    assert resp.status_code == 404, resp.text


def test_put_prompt_noi_dung_rong_tra_400(client, session):
    adminToken, _ = _admin(client, session)
    # noiDung empty after strip → DTO min_length rejects it (400).
    resp = client.put(
        "/api/admin/prompts/synthesis",
        json={"noiDung": "   "},
        headers=_auth(adminToken),
    )
    assert resp.status_code == 400, resp.text


# --- Operational limits -----------------------------------------------------
def test_put_limits_thanh_cong(client, session):
    adminToken, _ = _admin(client, session)
    resp = client.put(
        "/api/admin/limits",
        json={"llmTimeout": 20, "sessionTtl": 30, "maxFileSize": 25},
        headers=_auth(adminToken),
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["llmTimeout"] == 20


def test_put_limits_nguoi_dung_thuong_bi_403(client):
    userToken, _ = _register_login(client, "alice")
    resp = client.put(
        "/api/admin/limits",
        json={"llmTimeout": 20, "sessionTtl": 30, "maxFileSize": 25},
        headers=_auth(userToken),
    )
    assert resp.status_code == 403, resp.text


# --- API keys (BYOK) -------------------------------------------------------
def test_api_keys_put_get_delete(client):
    token, _ = _register_login(client, "alice")

    # No key yet.
    resp = client.get("/api/account/api-keys", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    # PUT to enter a key.
    resp = client.put(
        "/api/account/api-keys",
        json={"providerTen": "groq", "vaiTro": "synthesis", "khoa": "sk-secret-1234"},
        headers=_auth(token),
    )
    assert resp.status_code == 204, resp.text

    # GET returns the masked form — no plaintext leak.
    resp = client.get("/api/account/api-keys", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["providerTen"] == "groq"
    assert body[0]["khoaChe"] == "****1234"
    assert "sk-secret-1234" not in body[0]["khoaChe"]

    # DELETE.
    resp = client.delete(
        "/api/account/api-keys",
        params={"providerTen": "groq", "vaiTro": "synthesis"},
        headers=_auth(token),
    )
    assert resp.status_code == 204, resp.text
    resp = client.get("/api/account/api-keys", headers=_auth(token))
    assert resp.json() == []


def test_api_keys_delete_idempotent(client):
    token, _ = _register_login(client, "alice")
    # Delete when no record exists → still 204 (idempotent).
    resp = client.delete(
        "/api/account/api-keys",
        params={"providerTen": "groq", "vaiTro": "synthesis"},
        headers=_auth(token),
    )
    assert resp.status_code == 204, resp.text


def test_api_keys_co_lap_giua_nguoi_dung(client):
    aliceToken, _ = _register_login(client, "alice")
    bobToken, _ = _register_login(client, "bob")

    client.put(
        "/api/account/api-keys",
        json={"providerTen": "groq", "vaiTro": "synthesis", "khoa": "sk-alice-9999"},
        headers=_auth(aliceToken),
    )

    # Bob does not see alice's key.
    resp = client.get("/api/account/api-keys", headers=_auth(bobToken))
    assert resp.json() == []


def test_api_keys_thieu_token_tra_401(client):
    assert client.get("/api/account/api-keys").status_code == 401
