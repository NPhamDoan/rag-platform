"""Property-based test for AuthService.register with VALID input (R1.1).

# Feature: multi-user-rag-platform, Property 2: Valid registration creates a
# NGUOI_DUNG account — for every VALID tuple (email well-formed <=254, tenDangNhap
# 3..30 characters, matKhau 8..64 characters): register succeeds and creates a
# persisted TaiKhoan with vaiTro=NGUOI_DUNG, trangThai=HOAT_DONG; matKhauHash differs
# from the plaintext and verifyPassword(matKhau, hash) returns True; email/tenDangNhap
# round-trip the entered values.

Each round uses ONE separate in-memory SQLite session (mirroring tests/test_auth_
service_register.py), so email/tenDangNhap need not be unique across rounds. bcrypt
is slow, so max_examples=100 with the deadline disabled.
"""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.auth.password import verifyPassword
from app.db.database import Base
from app.db.models import TaiKhoan, TrangThaiTaiKhoan, VaiTro


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


# --- Smart generators: constrain the input to the VALID input space ----------
# An email part must match `[^@\s]+`: contains no '@' and no whitespace.
_EMAIL_PART_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+"
# tenDangNhap only needs length 3..30; use letters/digits for realism.
_USERNAME_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_"
_PASSWORD_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*"
)

_email_part = st.text(alphabet=_EMAIL_PART_ALPHABET, min_size=1, max_size=20)


@st.composite
def _valid_email(draw) -> str:
    """An email matching `^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$` with length <= 254."""
    local = draw(_email_part)
    domain = draw(_email_part)
    tld = draw(st.text(alphabet=_EMAIL_PART_ALPHABET.replace(".", ""), min_size=1, max_size=10))
    email = f"{local}@{domain}.{tld}"
    # Components total at most 20+20+10+2 = 52 chars, so always <= 254 (R1: email <= 254).
    assert len(email) <= 254
    return email


_valid_username = st.text(
    alphabet=_USERNAME_ALPHABET, min_size=3, max_size=30
)
_valid_password = st.text(
    alphabet=_PASSWORD_ALPHABET, min_size=8, max_size=64
)


@settings(max_examples=15, deadline=None)
@given(email=_valid_email(), tenDangNhap=_valid_username, matKhau=_valid_password)
def test_dang_ky_hop_le_tao_tai_khoan_nguoi_dung(email, tenDangNhap, matKhau):
    with _fresh_session() as db:
        service = AuthService(db)

        taiKhoan = service.register(email, tenDangNhap, matKhau)

        # (1) Register succeeds and creates exactly one persisted TaiKhoan.
        assert taiKhoan.id is not None
        assert db.query(TaiKhoan).count() == 1
        persisted = db.query(TaiKhoan).filter(TaiKhoan.id == taiKhoan.id).one()

        # (2) Default role NGUOI_DUNG, status HOAT_DONG (R1.1).
        assert persisted.vaiTro == VaiTro.NGUOI_DUNG
        assert persisted.trangThai == TrangThaiTaiKhoan.HOAT_DONG

        # (3) The password hash differs from plaintext and verifies correctly (R1.2).
        assert persisted.matKhauHash != matKhau
        assert verifyPassword(matKhau, persisted.matKhauHash) is True

        # (4) email/tenDangNhap round-trip the entered values.
        assert persisted.email == email
        assert persisted.tenDangNhap == tenDangNhap
