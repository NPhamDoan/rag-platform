"""Property-based test for AuthService.login — generic error message (R2.2).

# Feature: multi-user-rag-platform, Property 5: Thong bao loi dang nhap sai la
# chung chung va bat bien — voi moi kich ban that bai xac thuc (ten dang nhap
# KHONG ton tai, hoac tai khoan co that nhung SAI mat khau), AuthenticationError
# duoc nem ra co thong diep GIONG HET nhau va bang dung `_GENERIC_LOGIN_ERROR`.
# Nghia la thong diep KHONG tiet lo tai khoan co ton tai hay khong, cung khong
# tiet lo mat khau dung hay sai (R2.2).

Each round uses ITS OWN in-memory SQLite session (mirroring the other property tests),
registering exactly one account with a known password, then checking both failure branches.
bcrypt is slow, so limit max_examples=100 and disable the deadline.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService, _GENERIC_LOGIN_ERROR
from app.db.database import Base
from app.errors import AuthenticationError


@contextmanager
def _fresh_session():
    """Fresh in-memory SQLite session (schema from Base.metadata) — cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


# --- Smart generators: constrained to the valid input space -----------------
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
    tld = draw(
        st.text(alphabet=_EMAIL_PART_ALPHABET.replace(".", ""), min_size=1, max_size=10)
    )
    return f"{local}@{domain}.{tld}"


_valid_username = st.text(alphabet=_USERNAME_ALPHABET, min_size=3, max_size=30)
# Valid password (8..64) so registration succeeds.
_valid_password = st.text(alphabet=_PASSWORD_ALPHABET, min_size=8, max_size=64)


@settings(max_examples=15, deadline=None)
@given(
    email=_valid_email(),
    tenDangNhap=_valid_username,
    tenKhongTonTai=_valid_username,
    matKhauDung=_valid_password,
    matKhauSai=_valid_password,
)
def test_thong_bao_loi_dang_nhap_la_chung_chung_va_bat_bien(
    email, tenDangNhap, tenKhongTonTai, matKhauDung, matKhauSai
):
    # Separate the two failure branches:
    # - tenKhongTonTai must DIFFER from the registered username (so it really "does not exist").
    # - matKhauSai must DIFFER from the correct password (so it is really a "wrong password").
    assume(tenKhongTonTai != tenDangNhap)
    assume(matKhauSai != matKhauDung)

    with _fresh_session() as db:
        service = AuthService(db)
        service.register(email, tenDangNhap, matKhauDung)

        # Branch 1: the username does NOT exist.
        with pytest.raises(AuthenticationError) as exc_khong_ton_tai:
            service.login(tenKhongTonTai, matKhauDung)
        thongDiepKhongTonTai = str(exc_khong_ton_tai.value)

        # Branch 2: the account exists but the password is WRONG.
        with pytest.raises(AuthenticationError) as exc_sai_mat_khau:
            service.login(tenDangNhap, matKhauSai)
        thongDiepSaiMatKhau = str(exc_sai_mat_khau.value)

        # Invariant: both branches use the EXACT SAME generic message, revealing neither
        # whether the account exists nor whether the password is correct/wrong (R2.2).
        assert thongDiepKhongTonTai == _GENERIC_LOGIN_ERROR
        assert thongDiepSaiMatKhau == _GENERIC_LOGIN_ERROR
        assert thongDiepKhongTonTai == thongDiepSaiMatKhau
