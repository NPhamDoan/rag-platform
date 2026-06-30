"""Property-based test for `HistoryService` — isolated history, correct order, limit.

# Feature: multi-user-rag-platform, Property 17: Lich su tro chuyen co lap, dung thu tu va gioi han
# For a set of LichSuTroChuyen spanning multiple users, when a TaiKhoan lists the history
# of a space it has access to, the result contains ONLY that TaiKhoan's own entries, sorted
# by createdAt DESCENDING (newest first), and NO more than 50 entries; the delete operation
# succeeds ONLY on entries belonging to that same TaiKhoan (another user's entry → NotFoundError).
# Validates: Requirements 3.5, 3.7, 3.8, 9.3, 9.6, 9.7

Each round uses ITS OWN in-memory SQLite session (schema from Base.metadata). Hypothesis
generates: the number of question-answer turns for the tested account in the tested space
(may be > 50 to exercise the cap), plus "noise" turns from other users in the same space and
the account's own turns in a different space. The expected set is computed independently of the
implementation: filter by (taiKhoan, khongGian) → sort descending → truncate to 50.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import (
    KhongGianTaiLieu,
    LichSuTroChuyen,
    NhanXacMinh,
    TaiKhoan,
)
from app.errors import NotFoundError
from app.services.history_service import HistoryService

_MOC = datetime(2024, 1, 1, tzinfo=timezone.utc)


@contextmanager
def _fresh_session():
    """Fresh in-memory SQLite session — cleaned up after each round."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db = Session()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@st.composite
def _quan_the(draw):
    """Generate a population of question-answer turns (purely descriptive, no ORM yet)."""
    # Number of turns for the (owner, space) under test — allow > 50 to exercise the limit.
    soLuotChinh = draw(st.integers(min_value=0, max_value=60))
    # Number of other accounts that also add history to the SAME space under test.
    soNguoiKhac = draw(st.integers(min_value=0, max_value=3))
    luotNguoiKhac = draw(
        st.lists(
            st.integers(min_value=0, max_value=5),
            min_size=soNguoiKhac,
            max_size=soNguoiKhac,
        )
    )
    # Number of the owner's own turns but in a DIFFERENT space (must not be returned).
    soLuotKhongGianKhac = draw(st.integers(min_value=0, max_value=5))
    return {
        "soLuotChinh": soLuotChinh,
        "luotNguoiKhac": luotNguoiKhac,
        "soLuotKhongGianKhac": soLuotKhongGianKhac,
    }


def _tao_tai_khoan(db, ten):
    tk = TaiKhoan(email=f"{ten}@x.com", tenDangNhap=ten, matKhauHash="h")
    db.add(tk)
    db.commit()
    return tk


def _tao_khong_gian(db, chuSoHuu, ten, collection):
    kg = KhongGianTaiLieu(
        ten=ten,
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="e5",
        collectionName=collection,
    )
    db.add(kg)
    db.commit()
    return kg


def _tao_luot(db, taiKhoan, khongGian, cauHoi, phut):
    """Create ONE LichSuTroChuyen with a deterministic createdAt (to verify ordering)."""
    ls = LichSuTroChuyen(
        taiKhoanId=taiKhoan.id,
        khongGianId=khongGian.id,
        cauHoi=cauHoi,
        traLoi="dap",
        nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
        createdAt=_MOC + timedelta(minutes=phut),
    )
    db.add(ls)
    return ls


@settings(max_examples=100, deadline=None)
@given(quanThe=_quan_the())
def test_lich_su_co_lap_dung_thu_tu_gioi_han(quanThe):
    soLuotChinh = quanThe["soLuotChinh"]
    luotNguoiKhac = quanThe["luotNguoiKhac"]
    soLuotKhongGianKhac = quanThe["soLuotKhongGianKhac"]

    with _fresh_session() as db:
        chu = _tao_tai_khoan(db, "chu")
        kg = _tao_khong_gian(db, chu, "KG-chinh", "ws_chinh")
        kgKhac = _tao_khong_gian(db, chu, "KG-khac", "ws_khac")

        phut = 0  # increasing marker, each turn a distinct createdAt → deterministic order.

        # The owner's turns in the space under test — record the question labels in order.
        nhanChinh: list[str] = []
        for k in range(soLuotChinh):
            nhan = f"chinh-{k}"
            _tao_luot(db, chu, kg, nhan, phut)
            nhanChinh.append(nhan)
            phut += 1

        # Other users' turns in the SAME space (must not be returned for the owner).
        for i, soLuot in enumerate(luotNguoiKhac):
            nguoiKhac = _tao_tai_khoan(db, f"khac{i}")
            for k in range(soLuot):
                _tao_luot(db, nguoiKhac, kg, f"nguoikhac-{i}-{k}", phut)
                phut += 1

        # The owner's turns in a DIFFERENT space (must not be returned when listing the main space).
        for k in range(soLuotKhongGianKhac):
            _tao_luot(db, chu, kgKhac, f"khonggiankhac-{k}", phut)
            phut += 1

        db.commit()

        service = HistoryService(db)
        ketQua = service.listHistory(chu, kg)

        # Expected set (implementation-independent): the owner's turns in kg, newest
        # first, truncated to 50 → the last 50 labels reversed.
        nhanMongDoi = list(reversed(nhanChinh))[:50]
        nhanThucTe = [ls.cauHoi for ls in ketQua]

        # (1) Limit <= 50 entries (R9.3).
        assert len(nhanThucTe) <= 50
        # (2) Isolation + correct descending order: exactly match the expected label sequence
        #     (R3.5, R9.6 — no leakage to other users/spaces; R9.3 — ordering).
        assert nhanThucTe == nhanMongDoi
        # (3) createdAt strictly descending in the result.
        thoiGian = [ls.createdAt for ls in ketQua]
        assert thoiGian == sorted(thoiGian, reverse=True)

        # (4) Delete succeeds only on the owner's own entries (R3.8, R9.7).
        if ketQua:
            mucDau = ketQua[0]
            if luotNguoiKhac:
                # Create an "outsider" account to try deleting the owner's entry.
                nguoiNgoai = _tao_tai_khoan(db, "ngoai")
                with pytest.raises(NotFoundError):
                    service.deleteTurn(nguoiNgoai, mucDau.id)
                assert db.get(LichSuTroChuyen, mucDau.id) is not None
            # The owner deletes their own entry → succeeds.
            service.deleteTurn(chu, mucDau.id)
            assert db.get(LichSuTroChuyen, mucDau.id) is None
