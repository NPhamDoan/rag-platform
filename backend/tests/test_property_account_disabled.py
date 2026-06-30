"""Property-based test for disabled accounts (task 3.12, R10.7, R10.8).

# Feature: multi-user-rag-platform, Property 8: A disabled account cannot log in
# and its sessions are revoked — for every VALID credential set: after the account
# logs in successfully (obtaining a valid token) and is then set to status
# VO_HIEU_HOA, (1) every subsequent login (EVEN with the correct password) is
# rejected with AuthenticationError (R10.7), and (2) the previously valid token can
# NO longer be verified — verifyToken raises AuthenticationError because the account
# is no longer HOAT_DONG (R10.8).

Each round uses its OWN in-memory SQLite session (mirroring tests/test_auth_service_
login.py), so email/tenDangNhap need not be unique across rounds. bcrypt is slow,
so we cap max_examples=100 and disable the deadline.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.auth.tokens import verifyToken
from app.db.database import Base
from app.db.models import TaiKhoan, TrangThaiTaiKhoan
from app.errors import AuthenticationError


@contextmanager
def _fresh_session():
    """A fresh in-memory SQLite session (schema from Base.metadata) — cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


# --- Smart generators: constrained to the VALID credential space ----------
_EMAIL_PART_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+"
_USERNAME_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_PASSWORD_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
)

_email_part = st.text(alphabet=_EMAIL_PART_ALPHABET, min_size=1, max_size=20)


@st.composite
def _valid_email(draw) -> str:
    """Email matching `^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$` with length <= 254."""
    local = draw(_email_part)
    domain = draw(_email_part)
    tld = draw(st.text(alphabet=_EMAIL_PART_ALPHABET.replace(".", ""), min_size=1, max_size=10))
    return f"{local}@{domain}.{tld}"


_valid_username = st.text(alphabet=_USERNAME_ALPHABET, min_size=3, max_size=30)
_valid_password = st.text(alphabet=_PASSWORD_ALPHABET, min_size=8, max_size=64)


@settings(max_examples=15, deadline=None)
@given(email=_valid_email(), tenDangNhap=_valid_username, matKhau=_valid_password)
def test_tai_khoan_vo_hieu_hoa_khong_dang_nhap_va_thu_hoi_phien(email, tenDangNhap, matKhau):
    with _fresh_session() as db:
        service = AuthService(db)

        # 1) Register + log in successfully → valid token, verifiable.
        taiKhoan = service.register(email, tenDangNhap, matKhau)
        token, _vaiTro = service.login(tenDangNhap, matKhau)
        assert verifyToken(db, token).id == taiKhoan.id

        # 2) Disable the account.
        taiKhoan.trangThai = TrangThaiTaiKhoan.VO_HIEU_HOA
        db.commit()

        # (1) The next login — EVEN with the correct password — is rejected (R10.7).
        with pytest.raises(AuthenticationError):
            service.login(tenDangNhap, matKhau)

        # (2) The previous token can no longer be verified (account not HOAT_DONG, R10.8).
        with pytest.raises(AuthenticationError):
            verifyToken(db, token)
