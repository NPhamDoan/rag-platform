"""Property-based test for ConfigService — validating + applying CauHinhTruyXuat.

# Feature: multi-user-rag-platform, Property 48: Validate and apply CauHinhTruyXuat
# For every proposed CauHinhTruyXuat, the update succeeds IF AND ONLY IF every
# threshold is in [0,1] with the lower threshold <= the upper threshold, and k and
# the weights are valid; after the update, the next query (read back from the DB)
# sees the new values; "reset to defaults" reverts to the default values; an
# out-of-range config is rejected and KEEPS the existing values intact (no partial
# update).
# Validates: Requirements 19.1, 19.2, 19.3, 19.4

The valid ranges are enforced by the DTO `RetrievalConfigInput` (a single source of
truth): out-of-range values raise a pydantic `ValidationError` BEFORE reaching the
service, so the existing config is never partially modified. Each round uses ONE
separate in-memory SQLite engine (cleaned up after each round) and reads the config
back through a NEW Session to properly test "durability" (round-trip through the DB).
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
    NGUONG_MAX,
    NGUONG_MIN,
    RETRIEVAL_K_MAX,
    RETRIEVAL_K_MIN,
    get_settings,
)
from app.db.database import Base
from app.db.models import CauHinhTruyXuat, KhongGianTaiLieu, TaiKhoan
from app.models.schemas import RetrievalConfigInput
from app.services.config_service import ConfigService


@contextmanager
def _fresh_db():
    """A fresh in-memory SQLite engine + sessionmaker (schema from Base.metadata).

    Yields a `Session` factory to open MULTIPLE sessions on the same engine (reading
    the config back through a new session = a real round-trip through the DB).
    """
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    try:
        yield Session
    finally:
        engine.dispose()


def _seed_chu_va_khong_gian(Session) -> str:
    """Create owner + workspace + a default CauHinhTruyXuat record; return khongGianId."""
    db = Session()
    try:
        chu = TaiKhoan(email="chu@x.com", tenDangNhap="chu", matKhauHash="h")
        db.add(chu)
        db.commit()
        kg = KhongGianTaiLieu(
            ten="Du an",
            chuSoHuuId=chu.id,
            embeddingProvider="huggingface",
            collectionName="",
        )
        db.add(kg)
        db.flush()
        kg.collectionName = f"ws_{kg.id}"
        db.add(CauHinhTruyXuat(khongGianId=kg.id))
        db.commit()
        return kg.id
    finally:
        db.close()


# Generated values intentionally exceed the valid ranges ([0,1] for thresholds/weights,
# [1,100] for k) so each round may be VALID or INVALID — covering both branches.
_nguong = st.floats(min_value=-0.5, max_value=1.5, allow_nan=False, allow_infinity=False)
_trong_so = st.floats(min_value=-0.5, max_value=1.5, allow_nan=False, allow_infinity=False)
_k = st.integers(min_value=RETRIEVAL_K_MIN - 5, max_value=RETRIEVAL_K_MAX + 50)


def _la_hop_le(nguongDuoi, nguongTren, k, vector, bm25) -> bool:
    """An oracle independent of the DTO: uses the R19.1 criteria to decide valid/invalid."""
    return (
        NGUONG_MIN <= nguongDuoi <= NGUONG_MAX
        and NGUONG_MIN <= nguongTren <= NGUONG_MAX
        and nguongDuoi <= nguongTren
        and RETRIEVAL_K_MIN <= k <= RETRIEVAL_K_MAX
        and 0.0 <= vector <= 1.0
        and 0.0 <= bm25 <= 1.0
    )


@settings(max_examples=200, deadline=None)
@given(
    nguongDuoi=_nguong,
    nguongTren=_nguong,
    k=_k,
    vector=_trong_so,
    bm25=_trong_so,
)
def test_hop_le_hoa_va_ap_dung_cau_hinh_truy_xuat(nguongDuoi, nguongTren, k, vector, bm25):
    mongDoiHopLe = _la_hop_le(nguongDuoi, nguongTren, k, vector, bm25)

    with _fresh_db() as Session:
        khongGianId = _seed_chu_va_khong_gian(Session)

        # --- INVALID branch: the DTO rejects it, the existing config STAYS intact (R19.4) ---
        if not mongDoiHopLe:
            with pytest.raises(PydanticValidationError):
                RetrievalConfigInput(
                    nguongKhongTimThay=nguongDuoi,
                    nguongDuLienQuan=nguongTren,
                    k=k,
                    trongSoVector=vector,
                    trongSoBm25=bm25,
                )
            # Read back through a NEW session: the config is still the default (no partial update).
            db = Session()
            try:
                luu = db.get(CauHinhTruyXuat, khongGianId)
                mac_dinh = get_settings()
                assert luu.nguongKhongTimThay == pytest.approx(mac_dinh.nguong_khong_tim_thay)
                assert luu.nguongDuLienQuan == pytest.approx(mac_dinh.nguong_du_lien_quan)
                assert luu.k == mac_dinh.retrieval_k
                assert luu.trongSoVector == pytest.approx(mac_dinh.trong_so_vector)
                assert luu.trongSoBm25 == pytest.approx(mac_dinh.trong_so_bm25)
            finally:
                db.close()
            return

        # --- VALID branch: the DTO accepts it, updateRetrievalConfig persists durably (R19.1, R19.2) ---
        cfg = RetrievalConfigInput(
            nguongKhongTimThay=nguongDuoi,
            nguongDuLienQuan=nguongTren,
            k=k,
            trongSoVector=vector,
            trongSoBm25=bm25,
        )
        db = Session()
        try:
            chu = db.query(TaiKhoan).one()
            ConfigService(db).updateRetrievalConfig(chu, khongGianId, cfg)
        finally:
            db.close()

        # The next query (NEW session, read back from the DB) sees exactly the values just set.
        db = Session()
        try:
            luu = db.get(CauHinhTruyXuat, khongGianId)
            assert luu.nguongKhongTimThay == pytest.approx(nguongDuoi)
            assert luu.nguongDuLienQuan == pytest.approx(nguongTren)
            assert luu.k == k
            assert luu.trongSoVector == pytest.approx(vector)
            assert luu.trongSoBm25 == pytest.approx(bm25)
        finally:
            db.close()

        # --- Reset to defaults: revert to the default values (R19.3) ---
        db = Session()
        try:
            chu = db.query(TaiKhoan).one()
            ConfigService(db).resetRetrievalConfig(chu, khongGianId)
        finally:
            db.close()

        db = Session()
        try:
            sau = db.get(CauHinhTruyXuat, khongGianId)
            mac_dinh = get_settings()
            assert sau.nguongKhongTimThay == pytest.approx(mac_dinh.nguong_khong_tim_thay)
            assert sau.nguongDuLienQuan == pytest.approx(mac_dinh.nguong_du_lien_quan)
            assert sau.k == mac_dinh.retrieval_k
            assert sau.trongSoVector == pytest.approx(mac_dinh.trong_so_vector)
            assert sau.trongSoBm25 == pytest.approx(mac_dinh.trong_so_bm25)
        finally:
            db.close()
