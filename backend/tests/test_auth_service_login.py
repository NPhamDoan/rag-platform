"""Unit tests for AuthService.login (task 3.8, R2.1-4, R10.7).

Coverage:
- Successful login → returns (token, VaiTro) + creates a PhienXacThuc (expiry = session_ttl);
  resets the failure counter.
- Wrong password → a generic AuthenticationError + increments soLanDangNhapThatBai.
- 5 consecutive failures → the account is locked (khoaDenThoiDiem in the future).
- Locked account → rejected (LockedError) EVEN when the password is correct.
- Expired lock → allows logging in again (resets the counter).
- VO_HIEU_HOA account → rejected with a generic error (no disclosure).
- Non-existent account → the same generic error as a wrong password (R2.2).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService, _GENERIC_LOGIN_ERROR
from app.auth.tokens import verifyToken
from app.config import get_settings
from app.db.database import Base
from app.db.models import PhienXacThuc, TaiKhoan, TrangThaiTaiKhoan, VaiTro
from app.errors import AuthenticationError, LockedError


@pytest.fixture()
def session():
    """In-memory SQLite session with schema created from Base.metadata."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.fixture()
def service(session):
    return AuthService(session)


_MAT_KHAU = "matkhau123"


def _dang_ky(service, ten="userA", email="user@example.com", matKhau=_MAT_KHAU):
    return service.register(email, ten, matKhau)


# --- Successful login (R2.1) -----------------------------------------------
def test_login_dung_tra_token_va_vai_tro(service, session):
    tk = _dang_ky(service)

    token, vaiTro = service.login("userA", _MAT_KHAU)

    assert vaiTro == VaiTro.NGUOI_DUNG
    # Valid token → resolves to the correct account.
    assert verifyToken(session, token).id == tk.id
    # One authentication session has been created.
    assert session.query(PhienXacThuc).count() == 1


def test_login_dung_tao_phien_han_bang_session_ttl(service, session):
    _dang_ky(service)
    truoc = datetime.now(timezone.utc)

    service.login("userA", _MAT_KHAU)

    phien = session.query(PhienXacThuc).one()
    ttl = get_settings().session_ttl_minutes
    # expiresAt is approximately issuedAt + ttl (allow a few seconds of tolerance).
    mongDoi = truoc + timedelta(minutes=ttl)
    expiresAt = phien.expiresAt
    if expiresAt.tzinfo is None:
        expiresAt = expiresAt.replace(tzinfo=timezone.utc)
    assert abs((expiresAt - mongDoi).total_seconds()) < 60


def test_login_dung_reset_bo_dem_that_bai(service, session):
    tk = _dang_ky(service)
    # One prior failure.
    with pytest.raises(AuthenticationError):
        service.login("userA", "saimatkhau")
    assert tk.soLanDangNhapThatBai == 1

    service.login("userA", _MAT_KHAU)
    assert tk.soLanDangNhapThatBai == 0
    assert tk.khoaDenThoiDiem is None


# --- Wrong password (R2.2, R2.3) -------------------------------------------
def test_login_sai_mat_khau_loi_chung_va_tang_bo_dem(service, session):
    tk = _dang_ky(service)

    with pytest.raises(AuthenticationError) as exc:
        service.login("userA", "saimatkhau")

    assert str(exc.value) == _GENERIC_LOGIN_ERROR
    assert tk.soLanDangNhapThatBai == 1
    # No session is created on failure.
    assert session.query(PhienXacThuc).count() == 0


def test_login_tai_khoan_khong_ton_tai_loi_giong_sai_mat_khau(service):
    # R2.2: the same generic message, does not reveal whether the account exists.
    with pytest.raises(AuthenticationError) as exc:
        service.login("khongTonTai", _MAT_KHAU)
    assert str(exc.value) == _GENERIC_LOGIN_ERROR


# --- Lock after 5 failures (R2.4) ------------------------------------------
def test_login_khoa_sau_5_lan_that_bai_lien_tiep(service, session):
    tk = _dang_ky(service)
    maxFails = get_settings().login_max_fails

    for _ in range(maxFails):
        with pytest.raises(AuthenticationError):
            service.login("userA", "saimatkhau")

    assert tk.soLanDangNhapThatBai >= maxFails
    assert tk.khoaDenThoiDiem is not None
    khoaDen = tk.khoaDenThoiDiem
    if khoaDen.tzinfo is None:
        khoaDen = khoaDen.replace(tzinfo=timezone.utc)
    assert khoaDen > datetime.now(timezone.utc)


def test_login_khi_bi_khoa_tu_choi_du_dung_mat_khau(service, session):
    tk = _dang_ky(service)
    maxFails = get_settings().login_max_fails
    for _ in range(maxFails):
        with pytest.raises(AuthenticationError):
            service.login("userA", "saimatkhau")

    # Currently locked → a correct password is still rejected (LockedError).
    with pytest.raises(LockedError):
        service.login("userA", _MAT_KHAU)
    assert session.query(PhienXacThuc).count() == 0


def test_login_khoa_het_han_cho_dang_nhap_lai(service, session):
    tk = _dang_ky(service)
    maxFails = get_settings().login_max_fails
    for _ in range(maxFails):
        with pytest.raises(AuthenticationError):
            service.login("userA", "saimatkhau")

    # Simulate that the lock has expired.
    tk.khoaDenThoiDiem = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.commit()

    token, vaiTro = service.login("userA", _MAT_KHAU)
    assert verifyToken(session, token).id == tk.id
    assert tk.soLanDangNhapThatBai == 0
    assert tk.khoaDenThoiDiem is None


# --- Disabled account (R10.7) ----------------------------------------------
def test_login_tai_khoan_vo_hieu_hoa_bi_tu_choi(service, session):
    tk = _dang_ky(service)
    tk.trangThai = TrangThaiTaiKhoan.VO_HIEU_HOA
    session.commit()

    with pytest.raises(AuthenticationError) as exc:
        service.login("userA", _MAT_KHAU)

    # Generic error — does not reveal that the account is disabled.
    assert str(exc.value) == _GENERIC_LOGIN_ERROR
    assert session.query(PhienXacThuc).count() == 0
