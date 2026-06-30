"""Property test for AuthService.deleteOwnAccount (task 3.18, R25.6).

# Feature: multi-user-rag-platform, Property 58: Deleting your own account removes all
# data and revokes sessions.

Meaning: for EVERY registered account with varied related data (some sessions via login,
one or more KhongGianTaiLieu, a HanMuc created automatically at registration), calling
`deleteOwnAccount` MUST:
  - delete the TaiKhoan record,
  - leave no PhienXacThuc / KhongGianTaiLieu / HanMuc belonging to that account,
  - make every previously issued token no longer verifiable (AuthenticationError).

bcrypt is slow (hash at register + verify on every login), so we limit the number of
rounds and disable the deadline. Each example uses its own in-memory SQLite session
(mirroring tests/test_auth_service_password_lifecycle.py).
"""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings, strategies as st
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.auth.auth_service import AuthService
from app.auth.tokens import verifyToken
from app.db.database import Base
from app.db.models import (
    HanMuc,
    KhongGianTaiLieu,
    PhienXacThuc,
    TaiKhoan,
)
from app.errors import AuthenticationError

_MAT_KHAU = "matkhau123"


def _make_session():
    """A brand-new in-memory SQLite + schema from Base.metadata (fresh for each example)."""
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return engine, Session()


@settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    soPhien=st.integers(min_value=0, max_value=3),
    soKhongGian=st.integers(min_value=1, max_value=3),
)
def test_delete_own_account_xoa_toan_bo_du_lieu_va_thu_hoi_phien(soPhien, soKhongGian):
    engine, session = _make_session()
    try:
        service = AuthService(session)
        taiKhoan = service.register("user@example.com", "userA", _MAT_KHAU)
        taiKhoanId = taiKhoan.id

        # Create a varying number of sessions via login (each login = one PhienXacThuc + token).
        tokens = [service.login("userA", _MAT_KHAU)[0] for _ in range(soPhien)]

        # Create a varying number of spaces (related data to delete alongside).
        for i in range(soKhongGian):
            session.add(
                KhongGianTaiLieu(
                    ten=f"KG{i}",
                    chuSoHuuId=taiKhoanId,
                    embeddingProvider="huggingface",
                    collectionName=f"ws_{taiKhoanId}_{i}",
                )
            )
        session.commit()

        # Preconditions: HanMuc auto-created at register, session/space counts are correct.
        assert session.get(HanMuc, taiKhoanId) is not None
        assert (
            session.query(PhienXacThuc).filter_by(taiKhoanId=taiKhoanId).count()
            == soPhien
        )
        assert (
            session.query(KhongGianTaiLieu).filter_by(chuSoHuuId=taiKhoanId).count()
            == soKhongGian
        )

        service.deleteOwnAccount(taiKhoan)

        # The TaiKhoan record is gone.
        assert session.get(TaiKhoan, taiKhoanId) is None
        # No sessions / spaces / quota belong to the account anymore.
        assert (
            session.query(PhienXacThuc).filter_by(taiKhoanId=taiKhoanId).count() == 0
        )
        assert (
            session.query(KhongGianTaiLieu).filter_by(chuSoHuuId=taiKhoanId).count()
            == 0
        )
        assert session.get(HanMuc, taiKhoanId) is None
        # Every previously issued token can no longer be verified.
        for token in tokens:
            try:
                verifyToken(session, token)
                raise AssertionError("Token van xac minh duoc sau khi xoa tai khoan")
            except AuthenticationError:
                pass
    finally:
        session.close()
        engine.dispose()
