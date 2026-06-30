"""Unit tests for QuotaService (task 6.1, R12.1-12.7).

Coverage:
- checkAndReserve for EACH resource type, checking AT the boundary (R12.4):
  exactly at the limit → allowed; over the boundary → QuotaExceededError (R12.1-12.3).
- SO_TAI_LIEU requires khongGianId; a negative amount is rejected.
- setQuota: updates HanMuc (R12.5); a non-existent account → NotFoundError;
  out-of-range values are rejected by the HanMucInput DTO (R12.6).
- releaseQuota: no-op, does not change data.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError
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
from app.errors import NotFoundError, QuotaExceededError, ValidationError
from app.models.schemas import HanMucInput
from app.services.quota_service import LoaiTaiNguyen, QuotaService


@pytest.fixture()
def session():
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
    return QuotaService(session)


def _tao_tai_khoan(
    session,
    email="chu@x.com",
    ten="chu",
    *,
    soKhongGianToiDa=50,
    dungLuongToiDa=5 * 1024**3,
    soTaiLieuToiDaMoiKhongGian=1000,
) -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    tk.hanMuc = HanMuc(
        soKhongGianToiDa=soKhongGianToiDa,
        dungLuongToiDa=dungLuongToiDa,
        soTaiLieuToiDaMoiKhongGian=soTaiLieuToiDaMoiKhongGian,
    )
    session.add(tk)
    session.commit()
    return tk


def _tao_khong_gian(session, chuSoHuu, ten="KG") -> KhongGianTaiLieu:
    kg = KhongGianTaiLieu(
        ten=ten,
        moTa="",
        chuSoHuuId=chuSoHuu.id,
        embeddingProvider="huggingface",
        collectionName="ws_tmp",
    )
    session.add(kg)
    session.flush()
    kg.collectionName = f"ws_{kg.id}"
    session.commit()
    return kg


def _tao_tai_lieu(session, khongGian, kichThuoc=10) -> TaiLieu:
    tl = TaiLieu(
        khongGianId=khongGian.id,
        tenFile="a.pdf",
        dinhDang="pdf",
        kichThuoc=kichThuoc,
        trangThai=TrangThaiTaiLieu.NAP,
        chienLuocChunk="auto",
        soChunk=0,
    )
    session.add(tl)
    session.commit()
    return tl


# --- SO_KHONG_GIAN ----------------------------------------------------------
def test_reserve_so_khong_gian_tai_bien_cho_phep(service, session):
    chu = _tao_tai_khoan(session, soKhongGianToiDa=2)
    _tao_khong_gian(session, chu, "KG1")
    # 1 existing + 1 requested = 2 = limit → allowed (R12.4).
    service.checkAndReserve(chu.id, LoaiTaiNguyen.SO_KHONG_GIAN, 1)


def test_reserve_so_khong_gian_vuot_bien_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session, soKhongGianToiDa=2)
    _tao_khong_gian(session, chu, "KG1")
    _tao_khong_gian(session, chu, "KG2")
    # 2 existing + 1 = 3 > 2 → rejected (R12.1).
    with pytest.raises(QuotaExceededError):
        service.checkAndReserve(chu.id, LoaiTaiNguyen.SO_KHONG_GIAN, 1)


def test_reserve_so_khong_gian_doc_lap_theo_tai_khoan(service, session):
    chuA = _tao_tai_khoan(session, "a@x.com", "a", soKhongGianToiDa=1)
    chuB = _tao_tai_khoan(session, "b@x.com", "b", soKhongGianToiDa=1)
    _tao_khong_gian(session, chuA, "KG-A")
    # A's workspace does not count toward B's quota.
    service.checkAndReserve(chuB.id, LoaiTaiNguyen.SO_KHONG_GIAN, 1)


# --- DUNG_LUONG -------------------------------------------------------------
def test_reserve_dung_luong_tai_bien_cho_phep(service, session):
    chu = _tao_tai_khoan(session, dungLuongToiDa=100)
    kg = _tao_khong_gian(session, chu)
    _tao_tai_lieu(session, kg, kichThuoc=60)
    # 60 existing + 40 = 100 = limit → allowed (R12.4).
    service.checkAndReserve(chu.id, LoaiTaiNguyen.DUNG_LUONG, 40)


def test_reserve_dung_luong_vuot_bien_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session, dungLuongToiDa=100)
    kg = _tao_khong_gian(session, chu)
    _tao_tai_lieu(session, kg, kichThuoc=60)
    # 60 + 41 = 101 > 100 → rejected (R12.2).
    with pytest.raises(QuotaExceededError):
        service.checkAndReserve(chu.id, LoaiTaiNguyen.DUNG_LUONG, 41)


def test_reserve_dung_luong_tong_hop_nhieu_khong_gian(service, session):
    chu = _tao_tai_khoan(session, dungLuongToiDa=100)
    kg1 = _tao_khong_gian(session, chu, "KG1")
    kg2 = _tao_khong_gian(session, chu, "KG2")
    _tao_tai_lieu(session, kg1, kichThuoc=40)
    _tao_tai_lieu(session, kg2, kichThuoc=40)
    # Total 80 + 30 = 110 > 100 → rejected (aggregated across both workspaces).
    with pytest.raises(QuotaExceededError):
        service.checkAndReserve(chu.id, LoaiTaiNguyen.DUNG_LUONG, 30)


# --- SO_TAI_LIEU ------------------------------------------------------------
def test_reserve_so_tai_lieu_tai_bien_cho_phep(service, session):
    chu = _tao_tai_khoan(session, soTaiLieuToiDaMoiKhongGian=2)
    kg = _tao_khong_gian(session, chu)
    _tao_tai_lieu(session, kg)
    # 1 existing + 1 = 2 = limit → allowed (R12.4).
    service.checkAndReserve(chu.id, LoaiTaiNguyen.SO_TAI_LIEU, 1, khongGianId=kg.id)


def test_reserve_so_tai_lieu_vuot_bien_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session, soTaiLieuToiDaMoiKhongGian=2)
    kg = _tao_khong_gian(session, chu)
    _tao_tai_lieu(session, kg)
    _tao_tai_lieu(session, kg)
    # 2 + 1 = 3 > 2 → rejected (R12.3).
    with pytest.raises(QuotaExceededError):
        service.checkAndReserve(chu.id, LoaiTaiNguyen.SO_TAI_LIEU, 1, khongGianId=kg.id)


def test_reserve_so_tai_lieu_theo_tung_khong_gian(service, session):
    chu = _tao_tai_khoan(session, soTaiLieuToiDaMoiKhongGian=1)
    kg1 = _tao_khong_gian(session, chu, "KG1")
    kg2 = _tao_khong_gian(session, chu, "KG2")
    _tao_tai_lieu(session, kg1)
    # KG2 still has room even though KG1 is full → allowed (quota counted per workspace).
    service.checkAndReserve(chu.id, LoaiTaiNguyen.SO_TAI_LIEU, 1, khongGianId=kg2.id)


def test_reserve_so_tai_lieu_thieu_khong_gian_id_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session)
    with pytest.raises(ValidationError):
        service.checkAndReserve(chu.id, LoaiTaiNguyen.SO_TAI_LIEU, 1)


def test_reserve_luong_am_bi_tu_choi(service, session):
    chu = _tao_tai_khoan(session)
    with pytest.raises(ValidationError):
        service.checkAndReserve(chu.id, LoaiTaiNguyen.SO_KHONG_GIAN, -1)


# --- setQuota ---------------------------------------------------------------
def test_set_quota_cap_nhat_han_muc(service, session):
    chu = _tao_tai_khoan(session)
    hanMucMoi = HanMucInput(
        soKhongGianToiDa=10,
        dungLuongToiDa=1024**3,
        soTaiLieuToiDaMoiKhongGian=500,
        tanSuatTruyVanMoiPhut=30,
    )
    admin = _tao_tai_khoan(session, "admin@x.com", "admin")

    ketQua = service.setQuota(admin, chu.id, hanMucMoi)

    assert ketQua.soKhongGianToiDa == 10
    assert ketQua.dungLuongToiDa == 1024**3
    assert ketQua.soTaiLieuToiDaMoiKhongGian == 500
    assert ketQua.tanSuatTruyVanMoiPhut == 30
    # Persisted to the DB.
    assert session.get(HanMuc, chu.id).soKhongGianToiDa == 10


def test_set_quota_tai_khoan_khong_ton_tai_404(service, session):
    admin = _tao_tai_khoan(session, "admin@x.com", "admin")
    hanMucMoi = HanMucInput(
        soKhongGianToiDa=10,
        dungLuongToiDa=1024**3,
        soTaiLieuToiDaMoiKhongGian=500,
        tanSuatTruyVanMoiPhut=30,
    )
    with pytest.raises(NotFoundError):
        service.setQuota(admin, "khong-co", hanMucMoi)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"soKhongGianToiDa": 0},          # below QUOTA_SO_KHONG_GIAN_MIN (R12.6)
        {"soKhongGianToiDa": 1001},       # above QUOTA_SO_KHONG_GIAN_MAX
        {"dungLuongToiDa": 0},            # below QUOTA_DUNG_LUONG_MIN
        {"soTaiLieuToiDaMoiKhongGian": 0},  # below QUOTA_SO_TAI_LIEU_MIN
        {"tanSuatTruyVanMoiPhut": 0},     # < 1
    ],
)
def test_set_quota_ngoai_khoang_bi_DTO_tu_choi(kwargs):
    # R12.6: out-of-range values are rejected by the HanMucInput DTO before reaching the service.
    base = {
        "soKhongGianToiDa": 10,
        "dungLuongToiDa": 1024**3,
        "soTaiLieuToiDaMoiKhongGian": 500,
        "tanSuatTruyVanMoiPhut": 30,
    }
    base.update(kwargs)
    with pytest.raises(PydanticValidationError):
        HanMucInput(**base)


# --- releaseQuota -----------------------------------------------------------
def test_release_quota_no_op_khong_doi_du_lieu(service, session):
    chu = _tao_tai_khoan(session)
    kg = _tao_khong_gian(session, chu)
    _tao_tai_lieu(session, kg, kichThuoc=10)

    soKhongGianTruoc = session.query(KhongGianTaiLieu).count()
    soTaiLieuTruoc = session.query(TaiLieu).count()

    service.releaseQuota(chu.id, LoaiTaiNguyen.DUNG_LUONG, 10)

    # No rows created/deleted.
    assert session.query(KhongGianTaiLieu).count() == soKhongGianTruoc
    assert session.query(TaiLieu).count() == soTaiLieuTruoc
