"""Property-based test for changing the password revoking other sessions (task 3.14, R25.1).

# Feature: multi-user-rag-platform, Property 9: Changing the password revokes other
# sessions — for every VALID account with N login sessions (N varies), after
# changePassword with the CORRECT current password (passing the jtiHienTai of a
# "current" session): (1) the token of EVERY OTHER session can no longer be verified
# (AuthenticationError because the session was revoked), (2) the current session's
# token can STILL be verified, and (3) the old password can no longer log in.

Each round uses its OWN in-memory SQLite session (mirroring tests/test_auth_service_
password_lifecycle.py), so email/tenDangNhap need not be unique across rounds.
bcrypt is slow, so we cap max_examples=50, disable the deadline, and limit N (1..5) to
keep run time reasonable.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.auth.tokens import getTokenJti, verifyToken
from app.db.database import Base
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
@given(
    email=_valid_email(),
    tenDangNhap=_valid_username,
    matKhau=_valid_password,
    matKhauMoi=_valid_password,
    soPhien=st.integers(min_value=1, max_value=5),
    data=st.data(),
)
def test_doi_mat_khau_thu_hoi_phien_khac(
    email, tenDangNhap, matKhau, matKhauMoi, soPhien, data
):
    # The new password must differ from the old one so the "old password is rejected"
    # check is meaningful.
    assume(matKhau != matKhauMoi)

    with _fresh_session() as db:
        service = AuthService(db)
        taiKhoan = service.register(email, tenDangNhap, matKhau)

        # Create N sessions by logging in N times → N tokens with distinct jti.
        tokens = [service.login(tenDangNhap, matKhau)[0] for _ in range(soPhien)]
        jtis = [getTokenJti(t) for t in tokens]
        assert len(set(jtis)) == soPhien  # each session has its own jti

        # Pick one session as the "current" one.
        chiSoHienTai = data.draw(st.integers(min_value=0, max_value=soPhien - 1))
        tokenHienTai = tokens[chiSoHienTai]
        jtiHienTai = jtis[chiSoHienTai]

        service.changePassword(taiKhoan, matKhau, matKhauMoi, jtiHienTai=jtiHienTai)

        # (1) Every OTHER session is revoked → can no longer be verified.
        for i, token in enumerate(tokens):
            if i == chiSoHienTai:
                continue
            with pytest.raises(AuthenticationError):
                verifyToken(db, token)

        # (2) The current session is still valid.
        assert verifyToken(db, tokenHienTai).id == taiKhoan.id

        # (3) The old password can no longer log in.
        with pytest.raises(AuthenticationError):
            service.login(tenDangNhap, matKhau)
