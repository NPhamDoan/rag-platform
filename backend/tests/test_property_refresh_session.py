"""Property-based test for AuthService.refreshSession (R25.5).

# Feature: multi-user-rag-platform, Property 12: Refreshing a session issues a new
# valid token — for every VALID registered account (email well-formed <=254,
# tenDangNhap 3..30 characters, matKhau 8..64 characters), after logging in to get
# the old token and calling refreshSession(old token):
#   (1) the new token DIFFERS from the old one (token rotation),
#   (2) the new token verifyToken resolves to the SAME account (still valid),
#   (3) the old token can NO LONGER be verified (the old session has been revoked).

Each round uses ONE separate in-memory SQLite session (mirroring tests/test_auth_
service_login.py), so email/tenDangNhap need not be unique across rounds. bcrypt is
slow on register/login, so max_examples=60 with the deadline disabled.
"""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.auth.tokens import verifyToken
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


_valid_username = st.text(alphabet=_USERNAME_ALPHABET, min_size=3, max_size=30)
_valid_password = st.text(alphabet=_PASSWORD_ALPHABET, min_size=8, max_size=64)


@settings(max_examples=15, deadline=None)
@given(email=_valid_email(), tenDangNhap=_valid_username, matKhau=_valid_password)
def test_lam_moi_phien_cap_token_moi_hop_le(email, tenDangNhap, matKhau):
    with _fresh_session() as db:
        service = AuthService(db)
        taiKhoan = service.register(email, tenDangNhap, matKhau)
        idMongDoi = taiKhoan.id

        tokenCu, _vaiTro = service.login(tenDangNhap, matKhau)

        tokenMoi = service.refreshSession(tokenCu)

        # (1) The new token DIFFERS from the old one (token rotation).
        assert tokenMoi != tokenCu

        # (2) The new token is still valid → verifyToken resolves to the SAME account.
        assert verifyToken(db, tokenMoi).id == idMongDoi

        # (3) The old token has been revoked → can no longer be verified.
        try:
            verifyToken(db, tokenCu)
        except AuthenticationError:
            pass
        else:
            raise AssertionError(
                "verifyToken phai nem AuthenticationError voi token cu sau khi "
                "refreshSession thu hoi phien cu"
            )
