"""Unit tests for ShareService.grantShare / revokeShare (task 5.5, R11.1/11.4-11.7).

Coverage:
- grantShare: the owner grants CHI_DOC / GHI successfully; updates resolveAccess.
- grantShare: a non-owner → AuthorizationError (403, R11.7).
- grantShare: mucQuyen outside the valid set → ValidationError (400, R11.4).
- grantShare: a non-existent target account / workspace → NotFoundError (404, R11.5).
- grantShare: sharing with oneself → ValidationError (400).
- grantShare: re-granting updates mucQuyen (upsert, no UNIQUE violation).
- revokeShare: deletes the record → subsequent access is NONE (403/404).
- grant → revoke round-trip (R11.1, R11.6); revoking a non-existent share is idempotent.
"""

from __future__ import annotations

import enum

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db.models import ChiaSe, KhongGianTaiLieu, MucQuyen, TaiKhoan
from app.errors import AuthorizationError, NotFoundError, ValidationError
from app.services.share_service import MucTruyCap, ShareService


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


@pytest.fixture()
def service(session):
    return ShareService(session)


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


# --- grantShare valid ------------------------------------------------------
@pytest.mark.parametrize(
    "mucQuyen,muc_truy_cap_mong_doi",
    [
        (MucQuyen.CHI_DOC, MucTruyCap.CHI_DOC),
        (MucQuyen.GHI, MucTruyCap.GHI),
    ],
)
def test_grant_share_chu_so_huu_cap_quyen(
    service, session, mucQuyen, muc_truy_cap_mong_doi
):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)

    chiaSe = service.grantShare(chu, kg.id, khach.id, mucQuyen)

    assert chiaSe.khongGianId == kg.id
    assert chiaSe.taiKhoanId == khach.id
    assert chiaSe.mucQuyen == mucQuyen
    # After granting, the guest's resolveAccess reflects the correct permission.
    assert service.resolveAccess(khach, kg) == muc_truy_cap_mong_doi


# --- grantShare rejected --------------------------------------------------
def test_grant_share_khong_phai_chu_so_huu_403(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)

    with pytest.raises(AuthorizationError):
        service.grantShare(nguoiLa, kg.id, khach.id, MucQuyen.CHI_DOC)
    assert session.query(ChiaSe).count() == 0


def test_grant_share_nguoi_duoc_chia_se_khong_the_chia_se_tiep(service, session):
    # A person with WRITE permission (not the owner) cannot share further (R11.7).
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    nguoiMoi = _tao_tai_khoan(session, "moi@x.com", "moi")
    kg = _tao_khong_gian(session, chu)
    service.grantShare(chu, kg.id, khach.id, MucQuyen.GHI)

    with pytest.raises(AuthorizationError):
        service.grantShare(khach, kg.id, nguoiMoi.id, MucQuyen.CHI_DOC)


def test_grant_share_muc_quyen_ngoai_tap_400(service, session):
    # mucQuyen not in {CHI_DOC, GHI} → ValidationError. Use a fake enum to simulate it.
    class MucQuyenGia(str, enum.Enum):
        QUAN_TRI = "QUAN_TRI"

    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)

    with pytest.raises(ValidationError):
        service.grantShare(chu, kg.id, khach.id, MucQuyenGia.QUAN_TRI)
    assert session.query(ChiaSe).count() == 0


def test_grant_share_tai_khoan_dich_khong_ton_tai_404(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    kg = _tao_khong_gian(session, chu)

    with pytest.raises(NotFoundError):
        service.grantShare(chu, kg.id, "khong-co", MucQuyen.CHI_DOC)
    assert session.query(ChiaSe).count() == 0


def test_grant_share_khong_gian_khong_ton_tai_404(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")

    with pytest.raises(NotFoundError):
        service.grantShare(chu, "khong-co", khach.id, MucQuyen.CHI_DOC)


def test_grant_share_tu_chia_se_chinh_minh_400(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    kg = _tao_khong_gian(session, chu)

    with pytest.raises(ValidationError):
        service.grantShare(chu, kg.id, chu.id, MucQuyen.GHI)
    assert session.query(ChiaSe).count() == 0


# --- re-grant (upsert) ------------------------------------------------------
def test_re_grant_cap_nhat_muc_quyen(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)

    service.grantShare(chu, kg.id, khach.id, MucQuyen.CHI_DOC)
    # Re-grant with a higher permission: updates, does not create a second record.
    chiaSe = service.grantShare(chu, kg.id, khach.id, MucQuyen.GHI)

    assert chiaSe.mucQuyen == MucQuyen.GHI
    assert session.query(ChiaSe).filter(ChiaSe.khongGianId == kg.id).count() == 1
    assert service.resolveAccess(khach, kg) == MucTruyCap.GHI


# --- revokeShare ------------------------------------------------------------
def test_revoke_share_xoa_quyen_truy_cap(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)
    service.grantShare(chu, kg.id, khach.id, MucQuyen.GHI)
    assert service.resolveAccess(khach, kg) == MucTruyCap.GHI

    service.revokeShare(chu, kg.id, khach.id)

    assert session.query(ChiaSe).count() == 0
    # After revocation, the guest no longer has access → NONE (access will be 403/404).
    assert service.resolveAccess(khach, kg) == MucTruyCap.NONE


def test_grant_revoke_round_trip(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)

    assert service.resolveAccess(khach, kg) == MucTruyCap.NONE
    service.grantShare(chu, kg.id, khach.id, MucQuyen.CHI_DOC)
    assert service.resolveAccess(khach, kg) == MucTruyCap.CHI_DOC
    service.revokeShare(chu, kg.id, khach.id)
    # Back to the initial state (round-trip, R11.1/R11.6).
    assert service.resolveAccess(khach, kg) == MucTruyCap.NONE


def test_revoke_share_khong_ton_tai_idempotent(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    kg = _tao_khong_gian(session, chu)

    # Revoke when never shared → no error (idempotent).
    service.revokeShare(chu, kg.id, khach.id)
    assert session.query(ChiaSe).count() == 0


def test_revoke_share_khong_phai_chu_so_huu_403(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")
    nguoiLa = _tao_tai_khoan(session, "la@x.com", "la")
    kg = _tao_khong_gian(session, chu)
    service.grantShare(chu, kg.id, khach.id, MucQuyen.GHI)

    with pytest.raises(AuthorizationError):
        service.revokeShare(nguoiLa, kg.id, khach.id)
    # The record remains (not deleted by someone without permission).
    assert session.query(ChiaSe).count() == 1


def test_revoke_share_khong_gian_khong_ton_tai_404(service, session):
    chu = _tao_tai_khoan(session, "chu@x.com", "chu")
    khach = _tao_tai_khoan(session, "khach@x.com", "khach")

    with pytest.raises(NotFoundError):
        service.revokeShare(chu, "khong-co", khach.id)
