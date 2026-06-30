"""Property-based test for the password-reset link (R25.4).

# Feature: multi-user-rag-platform, Property 11: The password-reset link is
# single-use and expires. Models two independent properties of
# `AuthService.resetPassword` over a stateless reset token (signed with secret_key +
# matKhauHash, plus expiresAt):
#   (single-use) A VALID (unexpired) token succeeds once; REUSING the same token →
#     AuthenticationError (matKhauHash changed → signing key changed → signature mismatch).
#   (expiry) A token created with ttl <= 0 (expired) → AuthenticationError on the
#     very first use and does NOT change the password (still logs in with the old password).

Hypothesis generates a varied set of valid new passwords (8-64 chars) and varied
expired ttls (<= 0). Each example uses its own in-memory SQLite session to avoid
state contamination. bcrypt is slow → deadline=None, max_examples kept moderate.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.auth.password import verifyPassword
from app.auth.tokens import createResetToken
from app.db.database import Base
from app.db.models import TaiKhoan
from app.errors import AuthenticationError


def _tao_session():
    """Create a fresh in-memory SQLite session (mirroring the fixture in other tests)."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, Session()


_MAT_KHAU_CU = "matkhaucu123"

# A valid new password: 8-64 characters (per the system's validation rules).
_mat_khau_moi = st.text(min_size=8, max_size=64)
# An expired ttl: <= 0 minutes (negative or 0 → expiresAt <= now → expired).
_ttl_het_han = st.integers(min_value=-1440, max_value=0)


@settings(max_examples=15, deadline=None)
@given(matKhauMoi=_mat_khau_moi)
def test_reset_token_dung_mot_lan(matKhauMoi):
    """A valid token succeeds once; reusing it is rejected (R25.4)."""
    engine, session = _tao_session()
    try:
        service = AuthService(session)
        tk = service.register("user@example.com", "userA", _MAT_KHAU_CU)
        tokenReset = createResetToken(tk)  # default ttl → still valid

        # Attempt 1: reset succeeds → password changes to matKhauMoi.
        service.resetPassword(tokenReset, matKhauMoi)
        assert verifyPassword(matKhauMoi, tk.matKhauHash)

        # Attempt 2: REUSE the same token → rejected (single-use).
        try:
            service.resetPassword(tokenReset, "matkhaukhac999")
        except AuthenticationError:
            pass
        else:
            raise AssertionError(
                "resetPassword phai nem AuthenticationError khi dung lai token da tieu thu"
            )
        # The password is still matKhauMoi (the reuse attempt changed nothing).
        assert verifyPassword(matKhauMoi, tk.matKhauHash)
    finally:
        session.close()
        engine.dispose()


@settings(max_examples=15, deadline=None)
@given(matKhauMoi=_mat_khau_moi, ttlMinutes=_ttl_het_han)
def test_reset_token_het_han_bi_tu_choi_va_khong_doi_mat_khau(matKhauMoi, ttlMinutes):
    """An expired token is rejected and does NOT change the password (R25.4)."""
    engine, session = _tao_session()
    try:
        service = AuthService(session)
        tk = service.register("user@example.com", "userA", _MAT_KHAU_CU)
        hashCu = tk.matKhauHash
        tokenHetHan = createResetToken(tk, ttlMinutes=ttlMinutes)

        try:
            service.resetPassword(tokenHetHan, matKhauMoi)
        except AuthenticationError:
            pass
        else:
            raise AssertionError(
                "resetPassword phai nem AuthenticationError khi token da het han "
                f"(ttlMinutes={ttlMinutes})"
            )
        # The password does NOT change: the hash is unchanged, still verifies with the old password.
        assert tk.matKhauHash == hashCu
        assert verifyPassword(_MAT_KHAU_CU, tk.matKhauHash)
    finally:
        session.close()
        engine.dispose()
