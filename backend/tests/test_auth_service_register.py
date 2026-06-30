"""Unit tests for AuthService.register (task 3.5, R1).

Coverage:
- Valid registration → creates a TaiKhoan with VaiTro=NGUOI_DUNG, trangThai=HOAT_DONG,
  a default HanMuc, the password hashed (plaintext not stored) and verifying correctly.
- Duplicate email → ConflictError that names the "email" field; duplicate tenDangNhap →
  names the "tenDangNhap" field; neither creates a new TaiKhoan.
- Invalid input (malformed/too-long email, short/long tenDangNhap, short/long matKhau,
  missing/empty fields) → ValidationError, no TaiKhoan created.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.auth.password import verifyPassword
from app.db.database import Base
from app.db.models import HanMuc, TaiKhoan, TrangThaiTaiKhoan, VaiTro
from app.errors import ConflictError, ValidationError


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


# --- Valid registration -----------------------------------------------------
def test_register_hop_le_tao_tai_khoan_nguoi_dung(service, session):
    tk = service.register("user@example.com", "userA", "matkhau123")

    assert tk.id is not None
    assert tk.email == "user@example.com"
    assert tk.tenDangNhap == "userA"
    assert tk.vaiTro == VaiTro.NGUOI_DUNG
    assert tk.trangThai == TrangThaiTaiKhoan.HOAT_DONG
    # Only one account in the DB.
    assert session.query(TaiKhoan).count() == 1


def test_register_khong_luu_plaintext_va_xac_minh_dung(service):
    matKhau = "matkhau123"
    tk = service.register("user@example.com", "userA", matKhau)

    assert tk.matKhauHash != matKhau
    assert verifyPassword(matKhau, tk.matKhauHash) is True
    assert verifyPassword("saimatkhau", tk.matKhauHash) is False


def test_register_tao_han_muc_mac_dinh(service, session):
    tk = service.register("user@example.com", "userA", "matkhau123")

    hanMuc = session.query(HanMuc).filter(HanMuc.taiKhoanId == tk.id).one()
    assert hanMuc.soKhongGianToiDa == 50
    assert hanMuc.soTaiLieuToiDaMoiKhongGian == 1000


# --- Duplicate email / tenDangNhap (R1.3) ----------------------------------
def test_register_trung_email_bao_loi_neu_ro_truong(service, session):
    service.register("dup@example.com", "userA", "matkhau123")

    with pytest.raises(ConflictError) as exc:
        service.register("dup@example.com", "userB", "matkhau123")

    assert "email" in str(exc.value).lower()
    assert session.query(TaiKhoan).count() == 1


def test_register_trung_ten_dang_nhap_bao_loi_neu_ro_truong(service, session):
    service.register("a@example.com", "trungTen", "matkhau123")

    with pytest.raises(ConflictError) as exc:
        service.register("b@example.com", "trungTen", "matkhau123")

    assert "tendangnhap" in str(exc.value).lower()
    assert session.query(TaiKhoan).count() == 1


# --- Invalid input (R1.4, R1.5, R1.6, R1.7) -------------------------------
@pytest.mark.parametrize(
    "email,tenDangNhap,matKhau",
    [
        ("khong-phai-email", "userA", "matkhau123"),   # malformed email (R1.5)
        ("a@" + "x" * 260 + ".com", "userA", "matkhau123"),  # email too long (R1.5)
        ("user@example.com", "ab", "matkhau123"),       # tenDangNhap too short (R1.7)
        ("user@example.com", "x" * 31, "matkhau123"),   # tenDangNhap too long (R1.7)
        ("user@example.com", "userA", "short"),          # matKhau too short (R1.4)
        ("user@example.com", "userA", "x" * 65),         # matKhau too long (R1.4)
        ("", "userA", "matkhau123"),                     # empty email (R1.6)
        ("user@example.com", "", "matkhau123"),          # empty tenDangNhap (R1.6)
        ("user@example.com", "userA", ""),               # empty matKhau (R1.6)
    ],
)
def test_register_dau_vao_khong_hop_le_bi_tu_choi(
    service, session, email, tenDangNhap, matKhau
):
    with pytest.raises(ValidationError):
        service.register(email, tenDangNhap, matKhau)
    assert session.query(TaiKhoan).count() == 0
