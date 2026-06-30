"""Unit tests for the authentication/authorization DI dependencies (task 4.1).

Uses a minimal FastAPI app + TestClient to check the status-code mapping:
- `get_current_user`: missing token → 401, invalid token → 401, valid token → 200.
- `require_role`: NGUOI_DUNG calling a QUAN_TRI route → 403; QUAN_TRI → 200.
- `require_workspace_access`: owner / shared with sufficient permission → 200; insufficient
  permission (CHI_DOC but GHI required) → 403; no permission or non-existent workspace → 404.

Overrides `get_db` so every dependency shares the same in-memory SQLite Session with the
test data.
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import (
    get_current_user,
    get_db,
    require_role,
    require_workspace_access,
)
from app.api.middleware.error_handler import register_error_handlers
from app.auth.tokens import createToken
from app.db.database import Base
from app.db.models import ChiaSe, KhongGianTaiLieu, MucQuyen, TaiKhoan, VaiTro
from app.services.share_service import MucTruyCap


@pytest.fixture()
def session():
    # check_same_thread=False + StaticPool: TestClient runs handlers on a different thread
    # → the in-memory connection must be shared across threads.
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
    """Minimal app wiring the dependencies under test, overriding get_db = test session."""
    app = FastAPI()
    register_error_handlers(app)

    @app.get("/me")
    def doc_me(taiKhoan: TaiKhoan = Depends(get_current_user)) -> dict:
        return {"id": taiKhoan.id}

    @app.get("/admin")
    def chi_quan_tri(
        taiKhoan: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    ) -> dict:
        return {"id": taiKhoan.id}

    @app.get("/ws/{id}/read")
    def doc_khong_gian(
        khongGian: KhongGianTaiLieu = Depends(
            require_workspace_access(MucTruyCap.CHI_DOC)
        ),
    ) -> dict:
        return {"id": khongGian.id}

    @app.get("/ws/{id}/write")
    def ghi_khong_gian(
        khongGian: KhongGianTaiLieu = Depends(
            require_workspace_access(MucTruyCap.GHI)
        ),
    ) -> dict:
        return {"id": khongGian.id}

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    return TestClient(app)


# --- Helpers ----------------------------------------------------------------
def _tao_tai_khoan(session, email, ten, vaiTro=VaiTro.NGUOI_DUNG) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h", vaiTro=vaiTro)
    session.add(tk)
    session.commit()
    return tk


def _tao_khong_gian(session, chuSoHuu) -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten="KG", chuSoHuuId=chuSoHuu.id, embeddingProvider="e5", collectionName="ws_x"
    )
    session.add(kg)
    session.commit()
    return kg


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --- get_current_user -------------------------------------------------------
def test_thieu_token_tra_401(client):
    resp = client.get("/me")
    assert resp.status_code == 401


def test_token_sai_tra_401(client):
    resp = client.get("/me", headers=_auth("khong-phai-token-hop-le"))
    assert resp.status_code == 401


def test_token_hop_le_tra_200(client, session):
    tk = _tao_tai_khoan(session, "a@x.com", "userA")
    token = createToken(session, tk)

    resp = client.get("/me", headers=_auth(token))
    assert resp.status_code == 200
    assert resp.json()["id"] == tk.id


# --- require_role -----------------------------------------------------------
def test_nguoi_dung_goi_route_quan_tri_tra_403(client, session):
    tk = _tao_tai_khoan(session, "u@x.com", "userU", VaiTro.NGUOI_DUNG)
    token = createToken(session, tk)

    resp = client.get("/admin", headers=_auth(token))
    assert resp.status_code == 403


def test_quan_tri_goi_route_quan_tri_tra_200(client, session):
    tk = _tao_tai_khoan(session, "ad@x.com", "admin", VaiTro.QUAN_TRI)
    token = createToken(session, tk)

    resp = client.get("/admin", headers=_auth(token))
    assert resp.status_code == 200


def test_route_quan_tri_thieu_token_tra_401(client):
    resp = client.get("/admin")
    assert resp.status_code == 401


# --- require_workspace_access ----------------------------------------------
def test_chu_so_huu_doc_va_ghi_tra_200(client, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    kg = _tao_khong_gian(session, chu)
    token = createToken(session, chu)

    assert client.get(f"/ws/{kg.id}/read", headers=_auth(token)).status_code == 200
    assert client.get(f"/ws/{kg.id}/write", headers=_auth(token)).status_code == 200


def test_chia_se_chi_doc_doc_200_ghi_403(client, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "k@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    session.add(
        ChiaSe(khongGianId=kg.id, taiKhoanId=khach.id, mucQuyen=MucQuyen.CHI_DOC)
    )
    session.commit()
    token = createToken(session, khach)

    assert client.get(f"/ws/{kg.id}/read", headers=_auth(token)).status_code == 200
    # Insufficient permission (workspace visible but only CHI_DOC, GHI required) → 403.
    assert client.get(f"/ws/{kg.id}/write", headers=_auth(token)).status_code == 403


def test_khong_co_quyen_tra_404(client, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    kg = _tao_khong_gian(session, chu)
    token = createToken(session, nguoiLa)

    # NONE → 404 (does not disclose existence).
    assert client.get(f"/ws/{kg.id}/read", headers=_auth(token)).status_code == 404


def test_khong_gian_khong_ton_tai_tra_404(client, session):
    tk = _tao_tai_khoan(session, "a@x.com", "userA")
    token = createToken(session, tk)

    assert (
        client.get("/ws/khong-ton-tai/read", headers=_auth(token)).status_code == 404
    )


def test_workspace_access_thieu_token_tra_401(client, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    kg = _tao_khong_gian(session, chu)

    assert client.get(f"/ws/{kg.id}/read").status_code == 401
