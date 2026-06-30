"""Property-based test for quota config validation (Property 46).

# Feature: multi-user-rag-platform, Property 46: Hop le hoa cau hinh han muc —
# voi cac gia tri sinh ra cho soKhongGianToiDa, dungLuongToiDa,
# soTaiLieuToiDaMoiKhongGian, tanSuatTruyVanMoiPhut: viec dung DTO HanMucInput
# THANH CONG khi va chi khi MOI truong nam trong khoang hop le cua no
# (soKhongGian ∈ [QUOTA_SO_KHONG_GIAN_MIN, MAX], dungLuong ∈ [QUOTA_DUNG_LUONG_MIN,
# MAX], soTaiLieu ∈ [QUOTA_SO_TAI_LIEU_MIN, MAX], tanSuat >= 1); nguoc lai phai
# nem pydantic ValidationError (R12.6). Khoang hop le lay TRUC TIEP tu hang so
# trong app.config (khong hardcode). Bo sung: HanMucInput hop le duoc
# QuotaService.setQuota luu ben vung (R12.5).
# Validates: Requirements 12.5, 12.6

Generates values around the BOUNDARY of each range (below min, at min, in range, at max,
above max) to cover both valid and invalid cases. Each example builds the DTO and compares
against the prediction "valid iff every field is in range". When the DTO is valid, it also
checks that setQuota persists to the DB (its own in-memory SQLite session per round). Fast,
max_examples=200.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import (
    QUOTA_DUNG_LUONG_MAX,
    QUOTA_DUNG_LUONG_MIN,
    QUOTA_SO_KHONG_GIAN_MAX,
    QUOTA_SO_KHONG_GIAN_MIN,
    QUOTA_SO_TAI_LIEU_MAX,
    QUOTA_SO_TAI_LIEU_MIN,
)
from app.db.database import Base
from app.db.models import HanMuc, TaiKhoan
from app.models.schemas import HanMucInput
from app.services.quota_service import QuotaService


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


# Offset window around the boundary: enough to cover but without exceeding the storable range of
# SQLite INTEGER (64-bit). tanSuat has no upper bound in the DTO, so we cap the "valid" range at a
# realistic ceiling to avoid generating ints too large to store, while still exercising the range check.
_LECH = 1_000
_TAN_SUAT_TRAN = 1_000_000


def _quanh_bien(can_duoi: int, can_tren: int | None) -> st.SearchStrategy[int]:
    """Generate values around the boundary of a range [can_duoi, can_tren].

    Includes: below can_duoi, at can_duoi, in range, at can_tren, above can_tren — to cover
    both sides of the boundary. When `can_tren` is None (only the constraint >= can_duoi,
    e.g. tanSuat), drop the "above upper bound" branch and cap the valid range at
    `_TAN_SUAT_TRAN`. The out-of-range branches use the `_LECH` window so values stay within
    SQLite's storable range.
    """
    nhanh = [
        st.integers(min_value=can_duoi - _LECH, max_value=can_duoi - 1),  # below → invalid
        st.just(can_duoi),                                                # at lower bound → valid
    ]
    if can_tren is not None:
        nhanh.extend(
            [
                st.integers(min_value=can_duoi, max_value=can_tren),         # in range
                st.just(can_tren),                                           # at upper bound
                st.integers(min_value=can_tren + 1, max_value=can_tren + _LECH),  # above → invalid
            ]
        )
    else:
        nhanh.append(st.integers(min_value=can_duoi, max_value=_TAN_SUAT_TRAN))  # valid
    return st.one_of(*nhanh)


@settings(max_examples=40, deadline=None)
@given(
    soKhongGianToiDa=_quanh_bien(QUOTA_SO_KHONG_GIAN_MIN, QUOTA_SO_KHONG_GIAN_MAX),
    dungLuongToiDa=_quanh_bien(QUOTA_DUNG_LUONG_MIN, QUOTA_DUNG_LUONG_MAX),
    soTaiLieuToiDaMoiKhongGian=_quanh_bien(QUOTA_SO_TAI_LIEU_MIN, QUOTA_SO_TAI_LIEU_MAX),
    tanSuatTruyVanMoiPhut=_quanh_bien(1, None),
)
def test_hop_le_hoa_cau_hinh_han_muc(
    soKhongGianToiDa,
    dungLuongToiDa,
    soTaiLieuToiDaMoiKhongGian,
    tanSuatTruyVanMoiPhut,
):
    # Prediction: valid if and only if EVERY field is within its range.
    hopLe = (
        QUOTA_SO_KHONG_GIAN_MIN <= soKhongGianToiDa <= QUOTA_SO_KHONG_GIAN_MAX
        and QUOTA_DUNG_LUONG_MIN <= dungLuongToiDa <= QUOTA_DUNG_LUONG_MAX
        and QUOTA_SO_TAI_LIEU_MIN <= soTaiLieuToiDaMoiKhongGian <= QUOTA_SO_TAI_LIEU_MAX
        and tanSuatTruyVanMoiPhut >= 1
    )

    if not hopLe:
        # Out of range → the DTO rejects before reaching the service (R12.6).
        with pytest.raises(PydanticValidationError):
            HanMucInput(
                soKhongGianToiDa=soKhongGianToiDa,
                dungLuongToiDa=dungLuongToiDa,
                soTaiLieuToiDaMoiKhongGian=soTaiLieuToiDaMoiKhongGian,
                tanSuatTruyVanMoiPhut=tanSuatTruyVanMoiPhut,
            )
        return

    # Valid → the DTO is usable, and setQuota persists it (R12.5).
    hanMuc = HanMucInput(
        soKhongGianToiDa=soKhongGianToiDa,
        dungLuongToiDa=dungLuongToiDa,
        soTaiLieuToiDaMoiKhongGian=soTaiLieuToiDaMoiKhongGian,
        tanSuatTruyVanMoiPhut=tanSuatTruyVanMoiPhut,
    )

    with _fresh_session() as db:
        admin = TaiKhoan(email="admin@x.com", tenDangNhap="admin", matKhauHash="h")
        muctieu = TaiKhoan(email="u@x.com", tenDangNhap="u", matKhauHash="h")
        db.add_all([admin, muctieu])
        db.commit()

        banGhi = QuotaService(db).setQuota(admin, muctieu.id, hanMuc)

        assert banGhi.soKhongGianToiDa == soKhongGianToiDa
        assert banGhi.dungLuongToiDa == dungLuongToiDa
        assert banGhi.soTaiLieuToiDaMoiKhongGian == soTaiLieuToiDaMoiKhongGian
        assert banGhi.tanSuatTruyVanMoiPhut == tanSuatTruyVanMoiPhut
        # Persisted to the DB.
        luu = db.get(HanMuc, muctieu.id)
        assert luu is not None
        assert luu.soKhongGianToiDa == soKhongGianToiDa
        assert luu.tanSuatTruyVanMoiPhut == tanSuatTruyVanMoiPhut
