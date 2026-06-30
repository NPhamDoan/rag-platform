"""Unit tests for AuthService: logout / refresh / changePassword / reset / self-delete
account (task 3.13, R2.8, R25.1-6).

Coverage:
- logout: revokes the current session → the token can no longer be verified.
- refreshSession: issues a new valid token + revokes the old token.
- changePassword: wrong current password → rejected; new password too short → rejected;
  success → the old password stops working + revokes other sessions (keeps the current session).
- requestPasswordReset: generic response (None) for both an existing email and a non-existent one.
- resetPassword: single-use (the token cannot be reused) + expired is rejected +
  revokes all sessions.
- deleteOwnAccount: deletes the data (workspaces) + revokes/deletes all sessions.
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import (
    AuthService,
    _WRONG_CURRENT_PASSWORD,
)
from app.auth.tokens import (
    createResetToken,
    createToken,
    getTokenJti,
    verifyToken,
)
from app.db.database import Base
from app.db.models import (
    KhongGianTaiLieu,
    PhienXacThuc,
    TaiKhoan,
)
from app.errors import AuthenticationError, ValidationError


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


_MAT_KHAU = "matkhau123"
_MAT_KHAU_MOI = "matkhaumoi456"


def _dang_ky(service, ten="userA", email="user@example.com", matKhau=_MAT_KHAU):
    return service.register(email, ten, matKhau)


# --- logout (R2.8, R2.9) ----------------------------------------------------
def test_logout_thu_hoi_phien_hien_tai(service, session):
    _dang_ky(service)
    token, _ = service.login("userA", _MAT_KHAU)
    jti = getTokenJti(token)

    service.logout(jti)

    # The session is revoked → verify fails.
    assert session.get(PhienXacThuc, jti).revokedAt is not None
    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


def test_logout_idempotent_va_jti_khong_ton_tai(service):
    # Does not raise when the jti does not exist.
    service.logout("khong-ton-tai")


# --- refreshSession (R25.5) ------------------------------------------------
def test_refresh_cap_token_moi_hop_le(service, session):
    tk = _dang_ky(service)
    token, _ = service.login("userA", _MAT_KHAU)

    tokenMoi = service.refreshSession(token)

    assert tokenMoi != token
    # The new token is valid → resolves to the correct account.
    assert verifyToken(session, tokenMoi).id == tk.id


def test_refresh_thu_hoi_token_cu(service, session):
    _dang_ky(service)
    token, _ = service.login("userA", _MAT_KHAU)

    service.refreshSession(token)

    # The old token can no longer be used (revoked on rotation).
    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


def test_refresh_token_khong_hop_le_bi_tu_choi(service):
    with pytest.raises(AuthenticationError):
        service.refreshSession("token.khonghople")


# --- changePassword (R25.1) ------------------------------------------------
def test_change_password_sai_mat_khau_cu_bi_tu_choi(service):
    tk = _dang_ky(service)
    with pytest.raises(AuthenticationError) as exc:
        service.changePassword(tk, "saimatkhau", _MAT_KHAU_MOI)
    assert str(exc.value) == _WRONG_CURRENT_PASSWORD


def test_change_password_mat_khau_moi_qua_ngan_bi_tu_choi(service):
    tk = _dang_ky(service)
    with pytest.raises(ValidationError):
        service.changePassword(tk, _MAT_KHAU, "ngan")
    # The old password still works (unchanged).
    token, _ = service.login("userA", _MAT_KHAU)
    assert token


def test_change_password_thanh_cong_mat_khau_cu_het_tac_dung(service, session):
    tk = _dang_ky(service)

    service.changePassword(tk, _MAT_KHAU, _MAT_KHAU_MOI)

    # The old password can no longer be used to log in.
    with pytest.raises(AuthenticationError):
        service.login("userA", _MAT_KHAU)
    # The new password can log in.
    token, _ = service.login("userA", _MAT_KHAU_MOI)
    assert verifyToken(session, token).id == tk.id


def test_change_password_thu_hoi_cac_phien_khac_giu_phien_hien_tai(service, session):
    tk = _dang_ky(service)
    # Two sessions: one is the "current" one, the other is a different session.
    tokenKhac, _ = service.login("userA", _MAT_KHAU)
    tokenHienTai, _ = service.login("userA", _MAT_KHAU)
    jtiHienTai = getTokenJti(tokenHienTai)

    service.changePassword(tk, _MAT_KHAU, _MAT_KHAU_MOI, jtiHienTai=jtiHienTai)

    # The other session is revoked.
    with pytest.raises(AuthenticationError):
        verifyToken(session, tokenKhac)
    # The current session is still valid.
    assert verifyToken(session, tokenHienTai).id == tk.id


def test_change_password_khong_truyen_jti_thu_hoi_moi_phien(service, session):
    tk = _dang_ky(service)
    token, _ = service.login("userA", _MAT_KHAU)

    service.changePassword(tk, _MAT_KHAU, _MAT_KHAU_MOI)

    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


# --- requestPasswordReset (R25.2, R25.3) -----------------------------------
def test_request_reset_phan_hoi_chung_chung_email_co_thuc(service):
    _dang_ky(service)
    # Does not raise, returns None.
    assert service.requestPasswordReset("user@example.com") is None


def test_request_reset_phan_hoi_chung_chung_email_khong_ton_tai(service):
    # Identical response to the existing-email case (None, no error) → no disclosure.
    assert service.requestPasswordReset("khongtontai@example.com") is None


# --- resetPassword (R25.4) -------------------------------------------------
def test_reset_password_doi_mat_khau_va_dang_nhap_bang_mat_khau_moi(service, session):
    tk = _dang_ky(service)
    tokenReset = createResetToken(tk)

    service.resetPassword(tokenReset, _MAT_KHAU_MOI)

    with pytest.raises(AuthenticationError):
        service.login("userA", _MAT_KHAU)
    token, _ = service.login("userA", _MAT_KHAU_MOI)
    assert verifyToken(session, token).id == tk.id


def test_reset_password_single_use(service):
    tk = _dang_ky(service)
    tokenReset = createResetToken(tk)

    service.resetPassword(tokenReset, _MAT_KHAU_MOI)

    # Reusing the same token → rejected (matKhauHash has changed → signature mismatch).
    with pytest.raises(AuthenticationError):
        service.resetPassword(tokenReset, "matkhaukhac789")


def test_reset_password_het_han_bi_tu_choi(service):
    tk = _dang_ky(service)
    tokenHetHan = createResetToken(tk, ttlMinutes=-1)

    with pytest.raises(AuthenticationError):
        service.resetPassword(tokenHetHan, _MAT_KHAU_MOI)


def test_reset_password_thu_hoi_moi_phien(service, session):
    tk = _dang_ky(service)
    token, _ = service.login("userA", _MAT_KHAU)
    tokenReset = createResetToken(tk)

    service.resetPassword(tokenReset, _MAT_KHAU_MOI)

    with pytest.raises(AuthenticationError):
        verifyToken(session, token)


def test_reset_password_mat_khau_moi_qua_ngan_khong_tieu_thu_token(service):
    tk = _dang_ky(service)
    tokenReset = createResetToken(tk)

    # The new password is invalid → ValidationError, the hash is not changed.
    with pytest.raises(ValidationError):
        service.resetPassword(tokenReset, "ngan")
    # The token can still be used with a valid password.
    service.resetPassword(tokenReset, _MAT_KHAU_MOI)


# --- deleteOwnAccount (R25.6) ----------------------------------------------
def test_delete_own_account_xoa_du_lieu_va_thu_hoi_phien(service, session):
    tk = _dang_ky(service)
    token, _ = service.login("userA", _MAT_KHAU)
    taiKhoanId = tk.id
    # Create a workspace owned by the account (data that must be deleted along with it).
    ws = KhongGianTaiLieu(
        ten="KG", chuSoHuuId=taiKhoanId, embeddingProvider="huggingface",
        collectionName=f"ws_{taiKhoanId}",
    )
    session.add(ws)
    session.commit()

    service.deleteOwnAccount(tk)

    # The account + sessions + workspaces are all deleted (cascade).
    assert session.get(TaiKhoan, taiKhoanId) is None
    assert session.query(PhienXacThuc).filter_by(taiKhoanId=taiKhoanId).count() == 0
    assert session.query(KhongGianTaiLieu).filter_by(chuSoHuuId=taiKhoanId).count() == 0
    # The old token can no longer be verified.
    with pytest.raises(AuthenticationError):
        verifyToken(session, token)
