"""Property-based test for AuthService.login with a VALID registered account (R2.1).

# Feature: multi-user-rag-platform, Property 4: Dang nhap dung tao phien hop le
# kem vai tro — voi moi tai khoan da dang ky bang bo (email dung dinh dang <=254,
# tenDangNhap 3..30 ky tu, matKhau 8..64 ky tu) HOP LE: login bang dung thong tin
# (tenDangNhap + matKhau) tra ve mot token ma verifyToken xac minh ra DUNG tai khoan
# do, tra ve DUNG VaiTro cua tai khoan, va tao DUNG MOT PhienXacThuc.

Each round uses ITS OWN in-memory SQLite session (mirroring tests/test_auth_service_
login.py), so email/tenDangNhap need not be unique across rounds. bcrypt is slow, so
limit max_examples=100 and disable the deadline.
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
from app.db.models import PhienXacThuc, VaiTro


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


# --- Smart generators: constrained to the VALID input space -----------------
# Email parts must match `[^@\s]+`: no '@' and no whitespace.
_EMAIL_PART_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-+"
# tenDangNhap only needs length 3..30; use letters/digits for realism.
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
    email = f"{local}@{domain}.{tld}"
    # The parts are at most 20+20+10+2 = 52 characters, so always <= 254 (R1: email <= 254).
    assert len(email) <= 254
    return email


_valid_username = st.text(alphabet=_USERNAME_ALPHABET, min_size=3, max_size=30)
_valid_password = st.text(alphabet=_PASSWORD_ALPHABET, min_size=8, max_size=64)


@settings(max_examples=15, deadline=None)
@given(email=_valid_email(), tenDangNhap=_valid_username, matKhau=_valid_password)
def test_dang_nhap_dung_tao_phien_kem_vai_tro(email, tenDangNhap, matKhau):
    with _fresh_session() as db:
        service = AuthService(db)
        taiKhoan = service.register(email, tenDangNhap, matKhau)
        vaiTroMongDoi = taiKhoan.vaiTro
        idMongDoi = taiKhoan.id

        token, vaiTro = service.login(tenDangNhap, matKhau)

        # (1) Returns the account's correct VaiTro (R2.1).
        assert vaiTro == vaiTroMongDoi
        assert vaiTro == VaiTro.NGUOI_DUNG

        # (2) Valid token → verifyToken resolves to the exact registered account.
        assert verifyToken(db, token).id == idMongDoi

        # (3) Creates EXACTLY ONE PhienXacThuc, bound to the right account.
        phienList = db.query(PhienXacThuc).all()
        assert len(phienList) == 1
        assert phienList[0].taiKhoanId == idMongDoi
        assert phienList[0].revokedAt is None
