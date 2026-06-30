"""Unit tests for AdminService (task 12.5, R10.1-R10.6, R10.8).

Coverage:
- listAccounts: QUAN_TRI can retrieve all accounts together with vaiTro/trangThai (R10.1).
- disableAccount: disabling another account → VO_HIEU_HOA status + revoke its
  active sessions (R10.2, R10.8).
- disableAccount: an admin disabling itself → ValidationError, keeping the active
  status unchanged (R10.4).
- enableAccount: re-activating a disabled account → HOAT_DONG (R10.3).
- Operating on a non-existent account → NotFoundError (R10.5).
- NGUOI_DUNG requesting an admin operation → AuthorizationError (R10.6), nothing changes.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.tokens import createToken
from app.db.database import Base
from app.db.models import PhienXacThuc, TaiKhoan, TrangThaiTaiKhoan, VaiTro
from app.errors import AuthorizationError, NotFoundError, ValidationError
from app.services.admin_service import AdminService


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
    return AdminService(session)


def _tao_tai_khoan(
    session, email="u@x.com", ten="user", vaiTro=VaiTro.NGUOI_DUNG
) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h", vaiTro=vaiTro)
    session.add(tk)
    session.commit()
    return tk


def _admin(session) -> TaiKhoan:
    return _tao_tai_khoan(session, email="admin@x.com", ten="admin", vaiTro=VaiTro.QUAN_TRI)


def _so_phien_hoat_dong(session, taiKhoanId: str) -> int:
    return (
        session.query(PhienXacThuc)
        .filter(PhienXacThuc.taiKhoanId == taiKhoanId)
        .filter(PhienXacThuc.revokedAt.is_(None))
        .count()
    )


# --- R10.1 ------------------------------------------------------------------
def test_listAccounts_tra_toan_bo(service, session):
    admin = _admin(session)
    u1 = _tao_tai_khoan(session, email="a@x.com", ten="alice")
    u2 = _tao_tai_khoan(session, email="b@x.com", ten="bob")

    ket_qua = service.listAccounts(admin)

    ids = {tk.id for tk in ket_qua}
    assert ids == {admin.id, u1.id, u2.id}
    # Includes vaiTro + trangThai for each account (R10.1).
    theo_id = {tk.id: tk for tk in ket_qua}
    assert theo_id[admin.id].vaiTro == VaiTro.QUAN_TRI
    assert theo_id[u1.id].trangThai == TrangThaiTaiKhoan.HOAT_DONG


def test_listAccounts_nguoi_dung_bi_tu_choi(service, session):
    nguoiDung = _tao_tai_khoan(session)
    with pytest.raises(AuthorizationError):
        service.listAccounts(nguoiDung)


# --- R10.2, R10.8 -----------------------------------------------------------
def test_disableAccount_doi_trang_thai_va_thu_hoi_phien(service, session):
    admin = _admin(session)
    muc_tieu = _tao_tai_khoan(session, email="t@x.com", ten="target")
    # Create 2 active sessions for the target account.
    createToken(session, muc_tieu)
    createToken(session, muc_tieu)
    assert _so_phien_hoat_dong(session, muc_tieu.id) == 2

    service.disableAccount(admin, muc_tieu.id)

    session.refresh(muc_tieu)
    assert muc_tieu.trangThai == TrangThaiTaiKhoan.VO_HIEU_HOA
    # R10.8: every active session is revoked.
    assert _so_phien_hoat_dong(session, muc_tieu.id) == 0


# --- R10.4 ------------------------------------------------------------------
def test_disableAccount_khong_tu_vo_hieu(service, session):
    admin = _admin(session)
    with pytest.raises(ValidationError):
        service.disableAccount(admin, admin.id)
    # Keep the active status unchanged (R10.4).
    session.refresh(admin)
    assert admin.trangThai == TrangThaiTaiKhoan.HOAT_DONG


# --- R10.3 ------------------------------------------------------------------
def test_enableAccount_khoi_phuc_trang_thai(service, session):
    admin = _admin(session)
    muc_tieu = _tao_tai_khoan(session, email="t@x.com", ten="target")
    service.disableAccount(admin, muc_tieu.id)
    session.refresh(muc_tieu)
    assert muc_tieu.trangThai == TrangThaiTaiKhoan.VO_HIEU_HOA

    service.enableAccount(admin, muc_tieu.id)

    session.refresh(muc_tieu)
    assert muc_tieu.trangThai == TrangThaiTaiKhoan.HOAT_DONG


# --- R10.5 ------------------------------------------------------------------
def test_disableAccount_khong_ton_tai_raise_not_found(service, session):
    admin = _admin(session)
    with pytest.raises(NotFoundError):
        service.disableAccount(admin, "khong-co-that")


def test_enableAccount_khong_ton_tai_raise_not_found(service, session):
    admin = _admin(session)
    with pytest.raises(NotFoundError):
        service.enableAccount(admin, "khong-co-that")


# --- R10.6 ------------------------------------------------------------------
def test_disableAccount_nguoi_dung_bi_tu_choi(service, session):
    nguoiDung = _tao_tai_khoan(session, email="u@x.com", ten="user")
    muc_tieu = _tao_tai_khoan(session, email="t@x.com", ten="target")
    with pytest.raises(AuthorizationError):
        service.disableAccount(nguoiDung, muc_tieu.id)
    # No operation is performed (R10.6).
    session.refresh(muc_tieu)
    assert muc_tieu.trangThai == TrangThaiTaiKhoan.HOAT_DONG


def test_enableAccount_nguoi_dung_bi_tu_choi(service, session):
    nguoiDung = _tao_tai_khoan(session, email="u@x.com", ten="user")
    muc_tieu = _tao_tai_khoan(session, email="t@x.com", ten="target")
    with pytest.raises(AuthorizationError):
        service.enableAccount(nguoiDung, muc_tieu.id)
