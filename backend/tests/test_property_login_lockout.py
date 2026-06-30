"""Property-based test for AuthService.login — lockout after N failures (R2.4).

# Feature: multi-user-rag-platform, Property 6: Khoa dang nhap sau 5 lan that bai
# lien tiep — voi N lan dang nhap sai mat khau LIEN TIEP (N thay doi), tai khoan bi
# khoa KHI VA CHI KHI N >= login_max_fails. Khi bi khoa, login bi tu choi (LockedError)
# NGAY ca khi dung mat khau dung. Khi N < login_max_fails, tai khoan KHONG bi khoa va
# login voi mat khau dung van thanh cong (reset bo dem). Nguong doc tu Settings
# (login_max_fails), KHONG hardcode.

Each round uses ITS OWN in-memory SQLite session (registering one account), so there is
no need to worry about email/tenDangNhap collisions across rounds. bcrypt is slow (each
round hashes at registration + verifies many times), so limit max_examples=100, disable
the deadline, and narrow the N range around the threshold to keep the runtime reasonable.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.config import get_settings
from app.db.database import Base
from app.errors import AuthenticationError, LockedError

# Lockout threshold read from Settings (R2.4) — NOT hardcoded.
_MAX_FAILS = get_settings().login_max_fails

_TEN_DANG_NHAP = "userLock"
_EMAIL = "lock@example.com"
_MAT_KHAU_DUNG = "matkhauDung123"
_MAT_KHAU_SAI = "matkhauSai999"


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


def _con_hieu_luc(khoaDenThoiDiem) -> bool:
    """True if the lock timestamp is still in the future (treats a naive datetime as UTC)."""
    if khoaDenThoiDiem is None:
        return False
    if khoaDenThoiDiem.tzinfo is None:
        khoaDenThoiDiem = khoaDenThoiDiem.replace(tzinfo=timezone.utc)
    return khoaDenThoiDiem > datetime.now(timezone.utc)


# N varies around the threshold: covers below, at, and above the threshold. Capped at
# _MAX_FAILS + 3 to limit the number of bcrypt verifications and keep the runtime reasonable.
_so_lan_that_bai = st.integers(min_value=1, max_value=_MAX_FAILS + 3)


@settings(max_examples=15, deadline=None)
@given(soLanThatBai=_so_lan_that_bai)
def test_khoa_dang_nhap_khi_va_chi_khi_du_nguong(soLanThatBai):
    with _fresh_session() as db:
        service = AuthService(db)
        taiKhoan = service.register(_EMAIL, _TEN_DANG_NHAP, _MAT_KHAU_DUNG)

        # N consecutive failed logins. Before reaching the threshold → AuthenticationError;
        # after the lock (if N > threshold), subsequent attempts → LockedError.
        for _ in range(soLanThatBai):
            try:
                service.login(_TEN_DANG_NHAP, _MAT_KHAU_SAI)
                raise AssertionError("Dang nhap sai mat khau khong duoc thanh cong.")
            except (AuthenticationError, LockedError):
                pass

        biKhoa = soLanThatBai >= _MAX_FAILS

        # (1) The lock state on the record reflects the threshold condition correctly.
        assert _con_hieu_luc(taiKhoan.khoaDenThoiDiem) is biKhoa

        if biKhoa:
            # (2a) Locked → login is rejected EVEN with the correct password (R2.4).
            try:
                service.login(_TEN_DANG_NHAP, _MAT_KHAU_DUNG)
                raise AssertionError("Tai khoan bi khoa nhung van dang nhap duoc.")
            except LockedError:
                pass
        else:
            # (2b) Not locked → the correct password still logs in successfully, resetting the counter.
            token, _vaiTro = service.login(_TEN_DANG_NHAP, _MAT_KHAU_DUNG)
            assert token
            assert taiKhoan.soLanDangNhapThatBai == 0
            assert taiKhoan.khoaDenThoiDiem is None
