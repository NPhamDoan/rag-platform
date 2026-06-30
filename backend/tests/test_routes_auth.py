"""Tests for the authentication routes (task 13.1) — `/api/auth/*` + `DELETE /api/account`.

Uses a minimal FastAPI app containing just the auth router + the global error handler,
overriding `get_db` to an in-memory SQLite Session (no dependency on main.py / task 13.6).
Checks:

- register → login happy path (201 → 200 with token + vaiTro).
- login with wrong credentials → 401 (generic authentication error).
- an authenticated endpoint (DELETE /api/account) without a token → 401.
- self-deleting an account succeeds (204) → can no longer log in (401).

Validates: Requirements 1.1, 2.1, 2.8, 25.6
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
from app.db.database import Base


@pytest.fixture()
def session():
    # StaticPool + check_same_thread=False: TestClient runs the handler on a different
    # thread, so a single in-memory connection must be shared.
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

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


_VALID_REGISTER = {
    "email": "user@example.com",
    "tenDangNhap": "userOne",
    "matKhau": "matkhau123",
}


def _register(client) -> None:
    resp = client.post("/api/auth/register", json=_VALID_REGISTER)
    assert resp.status_code == 201, resp.text


def _login(client) -> str:
    resp = client.post(
        "/api/auth/login",
        json={"tenDangNhap": "userOne", "matKhau": "matkhau123"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_register_roi_login_happy_path(client):
    resp = client.post("/api/auth/register", json=_VALID_REGISTER)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "user@example.com"
    assert body["tenDangNhap"] == "userOne"
    assert "matKhau" not in body and "matKhauHash" not in body

    resp = client.post(
        "/api/auth/login",
        json={"tenDangNhap": "userOne", "matKhau": "matkhau123"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token"]
    assert body["vaiTro"] == "NGUOI_DUNG"


def test_login_sai_mat_khau_tra_401(client):
    _register(client)
    resp = client.post(
        "/api/auth/login",
        json={"tenDangNhap": "userOne", "matKhau": "saimatkhau"},
    )
    assert resp.status_code == 401


def test_endpoint_da_xac_thuc_thieu_token_tra_401(client):
    # DELETE /api/account requires a token → without a token it returns 401.
    assert client.delete("/api/account").status_code == 401


def test_logout_thieu_token_tra_401(client):
    assert client.post("/api/auth/logout").status_code == 401


def test_xoa_tai_khoan_roi_khong_dang_nhap_lai_duoc(client):
    _register(client)
    token = _login(client)

    resp = client.delete("/api/account", headers=_auth(token))
    assert resp.status_code == 204

    # The account has been deleted → logging in again fails (401).
    resp = client.post(
        "/api/auth/login",
        json={"tenDangNhap": "userOne", "matKhau": "matkhau123"},
    )
    assert resp.status_code == 401
