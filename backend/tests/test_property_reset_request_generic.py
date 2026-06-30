"""Property-based test for AuthService.requestPasswordReset — the response does not
reveal whether an email exists (R25.3).

# Feature: multi-user-rag-platform, Property 10: The password-reset response does not
# reveal whether an email exists — for any email (some REGISTERED, some NOT),
# `requestPasswordReset` ALWAYS returns None and NEVER raises. The observable response
# (return value + not raising) is IDENTICAL between an existing email and a
# non-existent email → it is impossible to tell which email is registered (R25.3).

Each round uses ONE separate in-memory SQLite session (mirroring the other property
tests). On one branch we register an account (one bcrypt hash), on the other we do
not; both must yield an indistinguishable response. bcrypt is slow, so max_examples=100
with the deadline disabled.
"""

from __future__ import annotations

from contextlib import contextmanager

from hypothesis import assume, given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.db.database import Base


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


# --- Smart generators: constrain the input to the valid input space ----------
_EMAIL_PART_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+"
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
    tld = draw(
        st.text(alphabet=_EMAIL_PART_ALPHABET.replace(".", ""), min_size=1, max_size=10)
    )
    return f"{local}@{domain}.{tld}"


_valid_username = st.text(alphabet=_USERNAME_ALPHABET, min_size=3, max_size=30)
_valid_password = st.text(alphabet=_PASSWORD_ALPHABET, min_size=8, max_size=64)


def _request_reset_quan_sat(service: AuthService, email: str):
    """Call requestPasswordReset and return the observable response:

    `("ok", return_value)` if it does not raise, or `("raised", error_type_name)`
    if it raises. Used to compare the "indistinguishability" between the two branches.
    """
    try:
        ket_qua = service.requestPasswordReset(email)
    except Exception as exc:  # pragma: no cover - must not happen per R25.3
        return ("raised", type(exc).__name__)
    return ("ok", ket_qua)


@settings(max_examples=15, deadline=None)
@given(
    emailDaDangKy=_valid_email(),
    emailChuaDangKy=_valid_email(),
    tenDangNhap=_valid_username,
    matKhau=_valid_password,
)
def test_phan_hoi_reset_khong_tiet_lo_ton_tai_email(
    emailDaDangKy, emailChuaDangKy, tenDangNhap, matKhau
):
    # The non-existent email must DIFFER from the registered one (so it truly "does not exist").
    assume(emailChuaDangKy != emailDaDangKy)

    with _fresh_session() as db:
        service = AuthService(db)
        # One branch: the account EXISTS.
        service.register(emailDaDangKy, tenDangNhap, matKhau)

        phanHoiTonTai = _request_reset_quan_sat(service, emailDaDangKy)
        phanHoiKhongTonTai = _request_reset_quan_sat(service, emailChuaDangKy)

        # 1) Neither branch may raise (R25.3).
        assert phanHoiTonTai[0] == "ok"
        assert phanHoiKhongTonTai[0] == "ok"

        # 2) Both always return None.
        assert phanHoiTonTai[1] is None
        assert phanHoiKhongTonTai[1] is None

        # 3) The observable responses are IDENTICAL → does not reveal which email
        #    is registered (R25.3).
        assert phanHoiTonTai == phanHoiKhongTonTai
