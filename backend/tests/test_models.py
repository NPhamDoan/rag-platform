"""Unit tests for ORM models + enums (task 2.1).

Coverage:
- `init_db()` / metadata correctly creates every declared table (R14.1).
- UNIQUE constraints: email, tenDangNhap, (khongGianId, taiKhoanId) (R1.1, R11.1).
- Enums stored by `value` (notably NhanXacMinh with diacritics) + round-trip through the DB.
- Defaults for CauHinhTruyXuat 0.3/0.5/k=8/0.5/0.5 and HanMuc 50/5GB/1000 (R12.1, R19).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db import models
from app.db.models import (
    CauHinhTruyXuat,
    ChiaSe,
    HanMuc,
    KhongGianTaiLieu,
    LichSuTroChuyen,
    MucQuyen,
    NhanXacMinh,
    TaiKhoan,
    TrangThaiTaiKhoan,
    VaiTro,
)


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


# Every ORM table expected to exist after the schema is created.
EXPECTED_TABLES = {
    "tai_khoan",
    "phien_xac_thuc",
    "khong_gian_tai_lieu",
    "chia_se",
    "tai_lieu",
    "chunk",
    "tom_tat_tai_lieu",
    "quy_tac_ranh_gioi",
    "cau_hinh_truy_xuat",
    "mau_prompt",
    "khoa_api_nguoi_dung",
    "han_muc",
    "lich_su_tro_chuyen",
    "trich_dan",
}


def test_metadata_tao_du_moi_bang():
    """All 14 entity tables are registered in Base.metadata."""
    assert EXPECTED_TABLES.issubset(set(Base.metadata.tables))


def test_create_all_tao_bang_trong_db(session):
    """The schema is actually created in the DB (inspector sees every table)."""
    inspector = inspect(session.get_bind())
    assert EXPECTED_TABLES.issubset(set(inspector.get_table_names()))


def _tao_tai_khoan(session, email="a@x.com", ten="userA") -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    session.add(tk)
    session.commit()
    return tk


def test_mac_dinh_tai_khoan(session):
    """Default VaiTro is NGUOI_DUNG, status HOAT_DONG, failed-attempt count = 0."""
    tk = _tao_tai_khoan(session)
    assert tk.vaiTro is VaiTro.NGUOI_DUNG
    assert tk.trangThai is TrangThaiTaiKhoan.HOAT_DONG
    assert tk.soLanDangNhapThatBai == 0
    assert tk.khoaDenThoiDiem is None
    assert tk.id  # UUID generated automatically


def test_unique_email(session):
    """UNIQUE(email) is violated on a duplicate email."""
    _tao_tai_khoan(session, email="dup@x.com", ten="u1")
    session.add(TaiKhoan(email="dup@x.com", tenDangNhap="u2", matKhauHash="h"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_unique_ten_dang_nhap(session):
    """UNIQUE(tenDangNhap) is violated on a duplicate login name."""
    _tao_tai_khoan(session, email="e1@x.com", ten="dupname")
    session.add(TaiKhoan(email="e2@x.com", tenDangNhap="dupname", matKhauHash="h"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_unique_chia_se(session):
    """UNIQUE(khongGianId, taiKhoanId) is violated on a duplicate share."""
    chuSoHuu = _tao_tai_khoan(session, email="own@x.com", ten="owner")
    nguoiDuocChiaSe = _tao_tai_khoan(session, email="tgt@x.com", ten="target")
    kg = KhongGianTaiLieu(
        ten="KG1", chuSoHuuId=chuSoHuu.id, embeddingProvider="hf", collectionName="ws_1"
    )
    session.add(kg)
    session.commit()

    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=nguoiDuocChiaSe.id, mucQuyen=MucQuyen.CHI_DOC))
    session.commit()
    session.add(ChiaSe(khongGianId=kg.id, taiKhoanId=nguoiDuocChiaSe.id, mucQuyen=MucQuyen.GHI))
    with pytest.raises(IntegrityError):
        session.commit()


def test_cau_hinh_truy_xuat_mac_dinh(session):
    """Defaults for CauHinhTruyXuat: 0.3 / 0.5 / k=8 / 0.5 / 0.5 (R19)."""
    tk = _tao_tai_khoan(session)
    kg = KhongGianTaiLieu(
        ten="KG", chuSoHuuId=tk.id, embeddingProvider="hf", collectionName="ws_x"
    )
    session.add(kg)
    session.commit()
    cfg = CauHinhTruyXuat(khongGianId=kg.id)
    session.add(cfg)
    session.commit()
    assert cfg.nguongKhongTimThay == 0.3
    assert cfg.nguongDuLienQuan == 0.5
    assert cfg.k == 8
    assert cfg.trongSoVector == 0.5
    assert cfg.trongSoBm25 == 0.5


def test_han_muc_mac_dinh(session):
    """Defaults for HanMuc: 50 / 5GB / 1000 (R12.1)."""
    tk = _tao_tai_khoan(session)
    hm = HanMuc(taiKhoanId=tk.id)
    session.add(hm)
    session.commit()
    assert hm.soKhongGianToiDa == 50
    assert hm.dungLuongToiDa == 5 * 1024**3
    assert hm.soTaiLieuToiDaMoiKhongGian == 1000


def test_nhan_xac_minh_luu_theo_value(session):
    """NhanXacMinh is stored by its `value` (with diacritics) and round-trips to the correct enum."""
    tk = _tao_tai_khoan(session)
    kg = KhongGianTaiLieu(
        ten="KG", chuSoHuuId=tk.id, embeddingProvider="hf", collectionName="ws_y"
    )
    session.add(kg)
    session.commit()
    ls = LichSuTroChuyen(
        taiKhoanId=tk.id,
        khongGianId=kg.id,
        cauHoi="hoi?",
        traLoi="dap.",
        nhanXacMinh=NhanXacMinh.DA_XAC_MINH,
    )
    session.add(ls)
    session.commit()

    # The value stored in the column is "đã xác minh" (the value, with diacritics)
    raw = session.execute(
        models.LichSuTroChuyen.__table__.select().with_only_columns(
            models.LichSuTroChuyen.__table__.c.nhanXacMinh
        )
    ).scalar_one()
    assert raw == "đã xác minh"
    assert ls.nhanXacMinh is NhanXacMinh.DA_XAC_MINH
    assert ls.nguonConKhaDung is True
