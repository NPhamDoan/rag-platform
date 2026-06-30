"""Property-based test for require_role(VaiTro.QUAN_TRI) — administrative operations
are reserved for QUAN_TRI (R10.6, R20.4).

# Feature: multi-user-rag-platform, Property 15: Administrative operations are
# reserved for QUAN_TRI — for EVERY active account with an arbitrary vaiTro
# (QUAN_TRI or NGUOI_DUNG), a route protected by Depends(require_role(VaiTro.QUAN_TRI))
# returns 200 IF AND ONLY IF vaiTro == QUAN_TRI; otherwise it returns 403. A request
# without a token always returns 401.

Each round uses its OWN in-memory SQLite session (StaticPool, check_same_thread=
False so TestClient runs the handler on a different thread), mirroring tests/test_dependencies.py.
Accounts are created directly with a fake matKhauHash (avoiding slow bcrypt); tokens
are issued via createToken. Capped at max_examples=100.
"""

from __future__ import annotations

from contextlib import contextmanager

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.dependencies import get_current_user, get_db, require_role
from app.api.middleware.error_handler import register_error_handlers
from app.auth.tokens import createToken
from app.db.database import Base
from app.db.models import TaiKhoan, VaiTro


@contextmanager
def _fresh_client():
    """Minimal app + TestClient mounting a QUAN_TRI-protected route, overriding get_db.

    Returns (client, session) so each round can create its own account and issue a
    token. Uses StaticPool + check_same_thread=False because TestClient runs the
    handler on a different thread.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = Session()

    app = FastAPI()
    register_error_handlers(app)

    @app.get("/admin")
    def chi_quan_tri(
        taiKhoan: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    ) -> dict:
        return {"id": taiKhoan.id}

    def _override_get_db():
        yield session

    app.dependency_overrides[get_db] = _override_get_db
    client = TestClient(app)
    try:
        yield client, session
    finally:
        session.close()
        engine.dispose()


# tenDangNhap only needs a valid length; letters/digits keep it realistic (does not affect authorization).
_USERNAME_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_valid_username = st.text(alphabet=_USERNAME_ALPHABET, min_size=3, max_size=30)


@settings(max_examples=40, deadline=None)
@given(vaiTro=st.sampled_from([VaiTro.QUAN_TRI, VaiTro.NGUOI_DUNG]), ten=_valid_username)
def test_thao_tac_quan_tri_chi_cho_quan_tri(vaiTro, ten):
    with _fresh_client() as (client, session):
        # Create an active account directly with a fake matKhauHash (avoiding bcrypt).
        taiKhoan = TaiKhoan(
            email=f"{ten}@example.com",
            tenDangNhap=ten,
            matKhauHash="hash-gia",
            vaiTro=vaiTro,
        )
        session.add(taiKhoan)
        session.commit()
        token = createToken(session, taiKhoan)

        resp = client.get("/admin", headers={"Authorization": f"Bearer {token}"})

        # 200 IF AND ONLY IF QUAN_TRI; otherwise 403 (R10.6, R20.4).
        if vaiTro == VaiTro.QUAN_TRI:
            assert resp.status_code == 200
            assert resp.json()["id"] == taiKhoan.id
        else:
            assert resp.status_code == 403

        # No token → 401, regardless of role.
        assert client.get("/admin").status_code == 401
