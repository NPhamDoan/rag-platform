"""Tests for the query + history routes (task 13.4).

Uses a minimal FastAPI app containing the auth + workspaces + query + history routers +
the global error handler. Overrides:
- `get_db` → a shared in-memory SQLite Session (no dependency on main.py).
- `get_query_pipeline` → a QueryPipeline injected with a FAKE LLM (synthesis/verification)
  + a FAKE Embedding_Provider + a FAKE Vector_Store (reusing the fakes validated in unit
  test 10.x) — to avoid real network/ChromaDB calls.

Coverage:
- query happy path: returns the answer + TrichDan, saves one history entry.
- exceeding the rate limit → 429 (low-frequency HanMuc), with NO LLM call.
- history lists only the caller's own entries (isolation), <=50, newest first.
- deleting one's own entry → 204; deleting another's / a non-existent entry → 404.
- missing token → 401; an outsider → 404 (does not reveal the workspace's existence).

Validates: Requirements 6.1, 9.3, 9.6, 24.2
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_db, get_query_pipeline
from app.api.middleware.error_handler import register_error_handlers
from app.api.middleware.rate_limit import get_rate_limiter
from app.api.routes.auth import router as auth_router
from app.api.routes.history import router as history_router
from app.api.routes.query import router as query_router
from app.api.routes.workspaces import router as workspaces_router
from app.db.database import Base
from app.db.models import HanMuc, LichSuTroChuyen, NhanXacMinh
from app.pipelines.query_pipeline import QueryPipeline
from app.storage.vector_store import META_TAI_LIEU_ID, SearchResult

# Reuse the fakes validated in the query pipeline unit tests (task 10.x).
from tests.test_query_pipeline_retrieve import FakeEmbeddingProvider, FakeVectorStore
from tests.test_query_pipeline_synthesize import FakeLLM


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
    # The rate limiter state lives for the whole process → reset on each test.
    get_rate_limiter().reset()

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(auth_router)
    app.include_router(workspaces_router)
    app.include_router(query_router)
    app.include_router(history_router)

    def _override_get_db():
        yield session

    def _override_get_query_pipeline():
        return QueryPipeline(
            session,
            verifyProvider=FakeLLM("da xac minh"),
            synthesisProvider=FakeLLM("Theo tai lieu, cau tra loi la abc [1]."),
            embeddingProvider=FakeEmbeddingProvider(),
            vectorStore=FakeVectorStore(
                [
                    SearchResult(
                        "c1",
                        "Noi dung dieu luat lien quan",
                        {META_TAI_LIEU_ID: "doc-1"},
                        score=0.9,
                    )
                ]
            ),
        )

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_query_pipeline] = _override_get_query_pipeline
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


def _share(client, token: str, wsId: str, taiKhoanMucTieuId: str, mucQuyen: str) -> None:
    resp = client.post(
        f"/api/workspaces/{wsId}/shares",
        json={"taiKhoanMucTieuId": taiKhoanMucTieuId, "mucQuyen": mucQuyen},
        headers=_auth(token),
    )
    assert resp.status_code == 201, resp.text


def _query(client, token: str, wsId: str, cauHoi: str = "Tốc độ tối đa là bao nhiêu?") -> dict:
    return client.post(
        f"/api/workspaces/{wsId}/query",
        json={"cauHoi": cauHoi},
        headers=_auth(token),
    )


def _set_rate_limit(session, taiKhoanId: str, tanSuat: int) -> None:
    """Set HanMuc.tanSuatTruyVanMoiPhut for the account (create one if missing)."""
    hm = session.get(HanMuc, taiKhoanId)
    if hm is None:
        hm = HanMuc(taiKhoanId=taiKhoanId)
        session.add(hm)
    hm.tanSuatTruyVanMoiPhut = tanSuat
    session.commit()


# --- Query happy path ------------------------------------------------------
def test_query_tra_cau_tra_loi_va_luu_lich_su(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    resp = _query(client, token, wsId)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "abc" in body["traLoi"]
    assert body["laFallback"] is False
    # Marker [1] → one TrichDan paralleling the first chunk.
    assert len(body["trichDan"]) == 1
    assert body["trichDan"][0]["marker"] == 1
    assert body["nhanXacMinh"] == NhanXacMinh.DA_XAC_MINH.value

    # One history entry has been saved.
    resp = client.get(f"/api/workspaces/{wsId}/history", headers=_auth(token))
    assert resp.status_code == 200, resp.text
    muc = resp.json()
    assert len(muc) == 1
    assert muc[0]["traLoi"] == body["traLoi"]
    assert muc[0]["nguonConKhaDung"] is True


def test_query_thieu_token_tra_401(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    resp = client.post(f"/api/workspaces/{wsId}/query", json={"cauHoi": "abc"})
    assert resp.status_code == 401, resp.text


def test_query_nguoi_ngoai_cuoc_tra_404(client):
    ownerToken, _ = _register_login(client, "owner")
    outsiderToken, _ = _register_login(client, "outsider")
    wsId = _create_workspace(client, ownerToken)

    resp = _query(client, outsiderToken, wsId)
    assert resp.status_code == 404, resp.text


def test_query_cau_hoi_rong_tra_400(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    resp = client.post(
        f"/api/workspaces/{wsId}/query",
        json={"cauHoi": "   "},
        headers=_auth(token),
    )
    # The QueryInput DTO trims whitespace → empty → 422/400; allow both variants.
    assert resp.status_code in (400, 422), resp.text


# --- Rate limit (R24.2) ----------------------------------------------------
def test_query_vuot_rate_limit_tra_429(client, session):
    token, accId = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    _set_rate_limit(session, accId, tanSuat=1)

    # First request OK.
    assert _query(client, token, wsId).status_code == 200
    # Second request exceeds the limit → 429 (before calling the LLM).
    resp = _query(client, token, wsId)
    assert resp.status_code == 429, resp.text


# --- History: isolation + 50-entry limit + newest first (R9.3, R9.6) ----------
def test_history_chi_cua_chinh_minh_co_lap(client):
    ownerToken, _ = _register_login(client, "owner")
    guestToken, guestId = _register_login(client, "guest")
    wsId = _create_workspace(client, ownerToken)
    _share(client, ownerToken, wsId, guestId, "CHI_DOC")

    # Owner queries twice, guest once, in the same workspace.
    assert _query(client, ownerToken, wsId).status_code == 200
    assert _query(client, ownerToken, wsId).status_code == 200
    assert _query(client, guestToken, wsId).status_code == 200

    owner_muc = client.get(
        f"/api/workspaces/{wsId}/history", headers=_auth(ownerToken)
    ).json()
    guest_muc = client.get(
        f"/api/workspaces/{wsId}/history", headers=_auth(guestToken)
    ).json()
    assert len(owner_muc) == 2
    assert len(guest_muc) == 1
    # No overlap: the guest's id is not in the owner's history.
    owner_ids = {m["id"] for m in owner_muc}
    assert guest_muc[0]["id"] not in owner_ids


def test_history_gioi_han_50_va_moi_nhat_truoc(client, session):
    token, accId = _register_login(client, "alice")
    wsId = _create_workspace(client, token)

    base = datetime(2024, 1, 1, 0, 0, 0)
    for i in range(60):
        session.add(
            LichSuTroChuyen(
                taiKhoanId=accId,
                khongGianId=wsId,
                cauHoi=f"cau hoi {i}",
                traLoi=f"tra loi {i}",
                nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
                createdAt=base + timedelta(seconds=i),
            )
        )
    session.commit()

    muc = client.get(
        f"/api/workspaces/{wsId}/history", headers=_auth(token)
    ).json()
    assert len(muc) == 50  # 50-entry limit (R9.3)
    # Newest first: createdAt descending.
    thoiGian = [m["createdAt"] for m in muc]
    assert thoiGian == sorted(thoiGian, reverse=True)
    # The first entry is the newest (i=59).
    assert muc[0]["cauHoi"] == "cau hoi 59"


# --- Delete history (R9.6-7) -----------------------------------------------
def test_delete_history_cua_minh_tra_204(client):
    token, _ = _register_login(client, "alice")
    wsId = _create_workspace(client, token)
    assert _query(client, token, wsId).status_code == 200

    muc = client.get(f"/api/workspaces/{wsId}/history", headers=_auth(token)).json()
    lichSuId = muc[0]["id"]

    resp = client.delete(f"/api/history/{lichSuId}", headers=_auth(token))
    assert resp.status_code == 204, resp.text

    # No entries remain.
    muc = client.get(f"/api/workspaces/{wsId}/history", headers=_auth(token)).json()
    assert muc == []


def test_delete_history_khong_ton_tai_tra_404(client):
    token, _ = _register_login(client, "alice")
    resp = client.delete("/api/history/khong-ton-tai", headers=_auth(token))
    assert resp.status_code == 404, resp.text


def test_delete_history_cua_nguoi_khac_tra_404(client):
    aliceToken, _ = _register_login(client, "alice")
    bobToken, _ = _register_login(client, "bob")
    wsId = _create_workspace(client, aliceToken)
    assert _query(client, aliceToken, wsId).status_code == 200

    lichSuId = client.get(
        f"/api/workspaces/{wsId}/history", headers=_auth(aliceToken)
    ).json()[0]["id"]

    # Bob deliberately deletes alice's entry → 404 (does not reveal its existence).
    resp = client.delete(f"/api/history/{lichSuId}", headers=_auth(bobToken))
    assert resp.status_code == 404, resp.text
