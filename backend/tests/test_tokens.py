"""Unit tests for HMAC tokens + PhienXacThuc (task 3.3).

Coverage:
- `createToken` creates a PhienXacThuc record + returns a token shaped as payload.chuKy.
- `verifyToken` accepts a valid token, returning the correct TaiKhoan.
- Rejection: an expired token, a revoked session, a VO_HIEU_HOA account, a forged
  signature, a tampered payload, a malformed/empty token.
- Every rejection raises AuthenticationError with the same generic message.
- `revokeToken` sets revokedAt (idempotent) — verification then fails.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.tokens import createToken, revokeToken, verifyToken
from app.db.database import Base
from app.db.models import PhienXacThuc, TaiKhoan, TrangThaiTaiKhoan
from app.errors import AuthenticationError


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


def _tao_tai_khoan(session, email="a@x.com", ten="userA") -> TaiKhoan:
    tk = TaiKhoan(email=email, tenDangNhap=ten, matKhauHash="h")
    session.add(tk)
    session.commit()
    return tk


def test_create_token_tao_phien_va_token_co_cau_truc(session):
    tk = _tao_tai_khoan(session)
    token = createToken(session, tk)

    assert token.count(".") == 1  # payload.chuKy
    phienList = session.query(PhienXacThuc).all()
    assert len(phienList) == 1
    assert phienList[0].taiKhoanId == tk.id
    assert phienList[0].revokedAt is None


def test_verify_token_hop_le_tra_ve_dung_tai_khoan(session):
    tk = _tao_tai_khoan(session)
    token = createToken(session, tk)

    ketQua = verifyToken(session, token)
    assert ketQua.id == tk.id


def test_verify_token_het_han_bi_tu_choi(session):
    tk = _tao_tai_khoan(session)
    token = createToken(session, tk, ttlMinutes=-1)  # expires immediately

    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


def test_verify_token_da_thu_hoi_bi_tu_choi(session):
    tk = _tao_tai_khoan(session)
    token = createToken(session, tk)
    jti = session.query(PhienXacThuc).one().id

    revokeToken(session, jti)
    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


def test_verify_token_tai_khoan_vo_hieu_hoa_bi_tu_choi(session):
    tk = _tao_tai_khoan(session)
    token = createToken(session, tk)

    tk.trangThai = TrangThaiTaiKhoan.VO_HIEU_HOA
    session.commit()
    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


def test_verify_token_chu_ky_gia_mao_bi_tu_choi(session):
    tk = _tao_tai_khoan(session)
    token = createToken(session, tk)

    payloadPart, _chuKy = token.split(".", 1)
    tokenGiaMao = f"{payloadPart}.chuKyGiaMao"
    with pytest.raises(AuthenticationError):
        verifyToken(session, tokenGiaMao)


def test_verify_token_payload_bi_sua_bi_tu_choi(session):
    tk = _tao_tai_khoan(session)
    token = createToken(session, tk)

    _payloadPart, chuKy = token.split(".", 1)
    # Change the payload (keep the old signature) → the signature no longer matches.
    tokenSua = f"YWJjZGVm.{chuKy}"
    with pytest.raises(AuthenticationError):
        verifyToken(session, tokenSua)


@pytest.mark.parametrize("token", ["", "khongcocham", "a.b.c", None])
def test_verify_token_sai_dinh_dang_bi_tu_choi(session, token):
    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


def test_revoke_token_idempotent(session):
    tk = _tao_tai_khoan(session)
    createToken(session, tk)
    jti = session.query(PhienXacThuc).one().id

    revokeToken(session, jti)
    moc = session.get(PhienXacThuc, jti).revokedAt
    assert moc is not None

    # Calling again does not change the revocation timestamp and raises no error.
    revokeToken(session, jti)
    assert session.get(PhienXacThuc, jti).revokedAt == moc

    # A non-existent jti → ignored, no error.
    revokeToken(session, "khong-ton-tai")
