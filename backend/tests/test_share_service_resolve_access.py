"""Unit tests for `resolveAccess` + `ShareService.resolveAccess` (task 4.1).

Covers the permission matrix (R3.2, R3.3, R11):
- Owner → CHU_SO_HUU.
- Shared CHI_DOC → CHI_DOC.
- Shared GHI → GHI.
- Not owner, not shared → NONE.
- The MucTruyCap ordering allows >= comparison (NONE < CHI_DOC < GHI < CHU_SO_HUU).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import ChiaSe, KhongGianTaiLieu, MucQuyen, TaiKhoan
from app.services.share_service import MucTruyCap, ShareService, resolveAccess


@pytest.fixture()
def session():
    """In-memory SQLite session with the schema created from Base.metadata."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _tao_tai_khoan(session, email, ten) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    session.add(tk)
    session.commit()
    return tk


def _tao_khong_gian(session, chuSoHuu) -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten="KG",
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="e5",
        collectionName="ws_x",
    )
    session.add(kg)
    session.commit()
    return kg


def _chia_se(session, khongGian, taiKhoan, mucQuyen) -> None:
    session.add(
        ChiaSe(khongGianId=khongGian.id, taiKhoanId=taiKhoan.id, mucQuyen=mucQuyen)
    )
    session.commit()


# --- Permission matrix ----------------------------------------------------------
def test_chu_so_huu_tra_chu_so_huu(session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    kg = _tao_khong_gian(session, chu)

    assert resolveAccess(session, chu, kg) == MucTruyCap.CHU_SO_HUU


def test_duoc_chia_se_chi_doc_tra_chi_doc(session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    _chia_se(session, kg, khach, MucQuyen.CHI_DOC)

    assert resolveAccess(session, khach, kg) == MucTruyCap.CHI_DOC


def test_duoc_chia_se_ghi_tra_ghi(session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    _chia_se(session, kg, khach, MucQuyen.GHI)

    assert resolveAccess(session, khach, kg) == MucTruyCap.GHI


def test_khong_quyen_tra_none(session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    kg = _tao_khong_gian(session, chu)

    assert resolveAccess(session, nguoiLa, kg) == MucTruyCap.NONE


def test_share_service_uy_thac_resolve_access(session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    kg = _tao_khong_gian(session, chu)

    service = ShareService(session)
    assert service.resolveAccess(chu, kg) == MucTruyCap.CHU_SO_HUU


# --- Enum ordering (allows >= comparison) -------------------------------------
def test_thu_tu_muc_truy_cap_tang_dan():
    assert (
        MucTruyCap.NONE
        < MucTruyCap.CHI_DOC
        < MucTruyCap.GHI
        < MucTruyCap.CHU_SO_HUU
    )
    # Minimum required permission: owner/write is enough for a write operation.
    assert MucTruyCap.GHI >= MucTruyCap.GHI
    assert MucTruyCap.CHU_SO_HUU >= MucTruyCap.GHI
    assert not (MucTruyCap.CHI_DOC >= MucTruyCap.GHI)
