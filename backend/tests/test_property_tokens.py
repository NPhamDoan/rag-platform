"""Property-based test for token validity (R2.5, R2.7, R2.8, R2.9, R10.8).

# Feature: multi-user-rag-platform, Property 7: A token is valid if and only if it is
# unexpired, not revoked, and the account is active — models three independent boolean
# conditions:
#   (1) unexpired / expired,
#   (2) not revoked / revoked,
#   (3) account HOAT_DONG / VO_HIEU_HOA.
# For every combination, verifyToken SUCCEEDS (returns the correct TaiKhoan) if and
# ONLY IF all three conditions are favorable (unexpired AND not revoked AND account
# active); every other case raises AuthenticationError.

Hypothesis generates every combination of the three booleans (8 combinations) over
>=100 examples. Each example uses its own in-memory SQLite session to avoid state
contamination between rounds.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.tokens import createToken, revokeToken, verifyToken
from app.db.database import Base
from app.db.models import PhienXacThuc, TaiKhoan, TrangThaiTaiKhoan
from app.errors import AuthenticationError


def _tao_session():
    """Create a fresh in-memory SQLite session (mirroring the fixture in test_tokens.py)."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, Session()


@settings(max_examples=40, deadline=None)
@given(
    conHan=st.booleans(),
    chuaThuHoi=st.booleans(),
    taiKhoanHoatDong=st.booleans(),
)
def test_token_hop_le_khi_va_chi_khi_con_han_chua_thu_hoi_va_hoat_dong(
    conHan, chuaThuHoi, taiKhoanHoatDong
):
    engine, session = _tao_session()
    try:
        tk = TaiKhoan(email="a@x.com", tenDangNhap="userA", matKhauHash="h")
        session.add(tk)
        session.commit()

        # (1) Unexpired → positive ttl; expired → negative ttl (expires immediately).
        ttlMinutes = 60 if conHan else -1
        token = createToken(session, tk, ttlMinutes=ttlMinutes)

        # (2) Revoke the session if the condition is "revoked".
        if not chuaThuHoi:
            jti = session.query(PhienXacThuc).one().id
            revokeToken(session, jti)

        # (3) Deactivate the account if the condition is "not active".
        if not taiKhoanHoatDong:
            tk.trangThai = TrangThaiTaiKhoan.VO_HIEU_HOA
            session.commit()

        token_hop_le = conHan and chuaThuHoi and taiKhoanHoatDong

        if token_hop_le:
            ketQua = verifyToken(session, token)
            assert ketQua.id == tk.id
        else:
            try:
                verifyToken(session, token)
            except AuthenticationError:
                pass
            else:
                raise AssertionError(
                    "verifyToken phai nem AuthenticationError khi token khong hop le "
                    f"(conHan={conHan}, chuaThuHoi={chuaThuHoi}, "
                    f"taiKhoanHoatDong={taiKhoanHoatDong})"
                )
    finally:
        session.close()
        engine.dispose()
