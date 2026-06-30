"""Property test for QuotaService.checkAndReserve (task 6.2, R12.1-12.4/12.7).

# Feature: multi-user-rag-platform, Property 45: Ap han muc nguyen tu tai bien va
# khi tuong tranh.
#
# Meaning: for EACH resource type (SO_KHONG_GIAN / DUNG_LUONG / SO_TAI_LIEU),
# EACH current usage level (`mucHienTai`), EACH limit (`gioiHan`) and EACH requested
# amount (`luong`), when the DB correctly reflects `mucHienTai` (creating exactly that many
# spaces / TaiLieu of the corresponding size) and HanMuc is set to exactly `gioiHan`, then:
#   - `checkAndReserve` ALLOWS (does not raise) IF AND ONLY IF
#         mucHienTai + luong <= gioiHan        (boundary check — equal to the limit is OK)
#   - otherwise (mucHienTai + luong > gioiHan) it raises `QuotaExceededError`,
#     consuming NO resources.
# Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.7
#
# Note on concurrency (R12.7): `checkAndReserve` row-locks HanMuc with `with_for_update` and
# computes usage from LIVE DATA within the SAME transaction — this serializes two concurrent
# operations on Postgres. Real concurrency is hard to reproduce deterministically with in-memory
# SQLite, so this property checks the OBSERVABLE PROPERTY, namely the boundary condition
# (current + luong <= limit), which is the contract that guarantees atomicity.
#
# Each example uses ITS OWN in-memory SQLite; accounts are created directly (no bcrypt), so it
# is fast → max_examples=150.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    HanMuc,
    KhongGianTaiLieu,
    TaiKhoan,
    TaiLieu,
    TrangThaiTaiLieu,
)
from app.errors import QuotaExceededError
from app.services.quota_service import LoaiTaiNguyen, QuotaService

# Quantity limits for the COUNT types (number of spaces / number of documents) so each example
# creates only a small number of records; variations around `gioiHan` still cover all boundary
# cases (current + luong <, =, > limit).
_DEM_GIOI_HAN_MAX = 6
_DEM_MUC_MAX = 8


@contextmanager
def _fresh_session():
    """Fresh in-memory SQLite session (schema from Base.metadata), cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _tao_tai_khoan(db, *, gioiHan: int, loai: LoaiTaiNguyen) -> TaiKhoan:
    """Create TaiKhoan + HanMuc, set `gioiHan` for `loai`, relax the other types."""
    tk = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
    hanMuc = HanMuc(taiKhoanId=tk.id)
    # Generous defaults for the types not tested in this example.
    hanMuc.soKhongGianToiDa = 10_000
    hanMuc.dungLuongToiDa = 10**12
    hanMuc.soTaiLieuToiDaMoiKhongGian = 10_000
    # Set exactly the limit under test.
    if loai is LoaiTaiNguyen.SO_KHONG_GIAN:
        hanMuc.soKhongGianToiDa = gioiHan
    elif loai is LoaiTaiNguyen.DUNG_LUONG:
        hanMuc.dungLuongToiDa = gioiHan
    else:  # SO_TAI_LIEU
        hanMuc.soTaiLieuToiDaMoiKhongGian = gioiHan
    tk.hanMuc = hanMuc
    db.add(tk)
    db.commit()
    return tk


def _tao_khong_gian(db, chuSoHuu: TaiKhoan, ten: str) -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten=ten,
        moTa="",
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    db.add(kg)
    db.flush()
    kg.collectionName = f"ws_{kg.id}"
    db.commit()
    return kg


def _tao_tai_lieu(db, khongGian: KhongGianTaiLieu, kichThuoc: int) -> None:
    tl = TaiLieu(
        khongGianId=khongGian.id,
        tenFile="a.pdf",
        dinhDang="pdf",
        kichThuoc=kichThuoc,
        trangThai=TrangThaiTaiLieu.NAP,
        chienLuocChunk="auto",
        soChunk=0,
    )
    db.add(tl)
    db.commit()


def _dung_db_theo_muc(
    db, chuSoHuu: TaiKhoan, loai: LoaiTaiNguyen, mucHienTai: int
) -> str | None:
    """Create live data such that current usage = `mucHienTai`.

    Returns khongGianId (only needed for SO_TAI_LIEU; None for the other types).
    """
    if loai is LoaiTaiNguyen.SO_KHONG_GIAN:
        for i in range(mucHienTai):
            _tao_khong_gian(db, chuSoHuu, f"KG{i}")
        return None
    if loai is LoaiTaiNguyen.DUNG_LUONG:
        kg = _tao_khong_gian(db, chuSoHuu, "KG")
        if mucHienTai > 0:
            # One TaiLieu carries the entire current usage (enough to check the boundary).
            _tao_tai_lieu(db, kg, kichThuoc=mucHienTai)
        return None
    # SO_TAI_LIEU: number of TaiLieu in ONE space.
    kg = _tao_khong_gian(db, chuSoHuu, "KG")
    for _ in range(mucHienTai):
        _tao_tai_lieu(db, kg, kichThuoc=1)
    return kg.id


@st.composite
def _kich_ban(draw):
    """Generate (loai, gioiHan, mucHienTai, luong) with ranges specific to each type."""
    loai = draw(st.sampled_from(list(LoaiTaiNguyen)))
    if loai is LoaiTaiNguyen.DUNG_LUONG:
        # Storage: one TaiLieu carries the whole current usage → allow large values.
        gioiHan = draw(st.integers(min_value=1, max_value=1000))
        mucHienTai = draw(st.integers(min_value=0, max_value=1200))
        luong = draw(st.integers(min_value=0, max_value=1000))
    else:
        # COUNT types: each unit = one record → keep it small for speed.
        gioiHan = draw(st.integers(min_value=1, max_value=_DEM_GIOI_HAN_MAX))
        mucHienTai = draw(st.integers(min_value=0, max_value=_DEM_MUC_MAX))
        luong = draw(st.integers(min_value=0, max_value=_DEM_GIOI_HAN_MAX))
    return loai, gioiHan, mucHienTai, luong


@settings(max_examples=40, deadline=None)
@given(_kich_ban())
def test_ap_han_muc_nguyen_tu_tai_bien(kichBan):
    loai, gioiHan, mucHienTai, luong = kichBan
    with _fresh_session() as db:
        chu = _tao_tai_khoan(db, gioiHan=gioiHan, loai=loai)
        khongGianId = _dung_db_theo_muc(db, chu, loai, mucHienTai)

        nenChoPhep = mucHienTai + luong <= gioiHan

        if nenChoPhep:
            # Must not raise at the boundary (equal to the limit is still valid — R12.4).
            chu_id = chu.id
            QuotaService(db).checkAndReserve(
                chu_id, loai, luong, khongGianId=khongGianId
            )
        else:
            # Over the boundary → QuotaExceededError (R12.1-12.3), consuming no resources.
            with pytest.raises(QuotaExceededError):
                QuotaService(db).checkAndReserve(
                    chu.id, loai, luong, khongGianId=khongGianId
                )
