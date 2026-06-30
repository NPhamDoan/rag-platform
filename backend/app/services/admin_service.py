"""AdminService — account administration (task 12.5, R10).

Implements the "AdminService" section of design.md:

    class AdminService:  # R10
        def listAccounts(self, admin) -> list[TaiKhoan]: ...          # R10.1
        def disableAccount(self, admin, taiKhoanId) -> None: ...      # R10.2, R10.4, R10.8
        def enableAccount(self, admin, taiKhoanId) -> None: ...       # R10.3

Principles:
- Every operation is an ADMIN OPERATION → `VaiTro.QUAN_TRI` only; the NGUOI_DUNG role
  is rejected with `AuthorizationError` (403) and NOTHING is changed (R10.6). Permission
  is checked BEFORE touching any data — consistent with `ConfigService._yeuCauQuanTri`.
- `listAccounts`: returns every `TaiKhoan` with id / vaiTro / trangThai (R10.1).
- `disableAccount`:
  - Target account does not exist → `NotFoundError` (404, R10.5).
  - A QUAN_TRI disabling itself → rejected with `ValidationError`, keeping the active
    state UNCHANGED (R10.4). Checked BEFORE any edit so the state is never touched.
  - Valid → set `trangThai = VO_HIEU_HOA` and revoke every active `PhienXacThuc` of the
    target account (R10.8) via `revokeAllSessions` — old tokens are rejected
    immediately (401) without waiting to expire.
- `enableAccount`: target account does not exist → `NotFoundError`; otherwise set
  `trangThai = HOAT_DONG` (R10.3).

Logging goes through the central logger; errors are NEVER swallowed silently. Sensitive
data (email / full username) is NEVER logged — only account id + state.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.auth.tokens import revokeAllSessions
from app.db.models import TaiKhoan, TrangThaiTaiKhoan, VaiTro
from app.errors import AuthorizationError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

_KHONG_DU_QUYEN_QUAN_TRI = "Can quyen QUAN_TRI de thuc hien thao tac quan tri nay."
_TAI_KHOAN_KHONG_TON_TAI = "Khong tim thay tai khoan."
_KHONG_TU_VO_HIEU = "Khong the tu vo hieu hoa tai khoan cua chinh minh."


class AdminService:
    """Account administration service operating on a single SQLAlchemy `Session`.

    Every operation requires `VaiTro.QUAN_TRI` (R10.6).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Admin permission check (R10.6) -------------------------------------
    def _yeuCauQuanTri(self, admin: TaiKhoan) -> None:
        """Ensure `admin` has `VaiTro.QUAN_TRI`; otherwise → `AuthorizationError`.

        Permission is checked BEFORE changing any data so that NGUOI_DUNG is rejected
        (403) WITHOUT the operation being performed (R10.6).
        """
        if admin.vaiTro != VaiTro.QUAN_TRI:
            logger.info(
                "Tu choi thao tac quan tri: tai khoan id=%s vai tro=%s, can QUAN_TRI.",
                admin.id,
                admin.vaiTro.value,
            )
            raise AuthorizationError(_KHONG_DU_QUYEN_QUAN_TRI)

    def _layTaiKhoanDich(self, taiKhoanId: str) -> TaiKhoan:
        """Fetch the target account; if it does not exist → `NotFoundError` (404, R10.5)."""
        taiKhoan = self.db.get(TaiKhoan, taiKhoanId)
        if taiKhoan is None:
            logger.info("Thao tac quan tri nham tai khoan khong ton tai: id=%s", taiKhoanId)
            raise NotFoundError(_TAI_KHOAN_KHONG_TON_TAI)
        return taiKhoan

    # --- R10.1: list accounts -----------------------------------------------
    def listAccounts(self, admin: TaiKhoan) -> list[TaiKhoan]:
        """Return every `TaiKhoan` with id / vaiTro / trangThai (R10.1) — QUAN_TRI only."""
        self._yeuCauQuanTri(admin)
        taiKhoanList = self.db.query(TaiKhoan).order_by(TaiKhoan.createdAt).all()
        logger.info("Liet ke tai khoan: admin id=%s, so tai khoan=%d", admin.id, len(taiKhoanList))
        return taiKhoanList

    # --- R10.2, R10.4, R10.8: disable ---------------------------------------
    def disableAccount(self, admin: TaiKhoan, taiKhoanId: str) -> None:
        """Disable an account + revoke its sessions (R10.2, R10.4, R10.8) — QUAN_TRI only.

        Disabling oneself → `ValidationError`, keeping the state UNCHANGED (R10.4).
        Target account does not exist → `NotFoundError` (R10.5). Valid → set the state
        to VO_HIEU_HOA then revoke every active `PhienXacThuc` (R10.8).
        """
        self._yeuCauQuanTri(admin)

        # R10.4: check BEFORE any lookup/edit so the state is never touched.
        if taiKhoanId == admin.id:
            logger.info("Tu choi vo hieu hoa: admin id=%s tu vo hieu chinh minh.", admin.id)
            raise ValidationError(_KHONG_TU_VO_HIEU)

        taiKhoan = self._layTaiKhoanDich(taiKhoanId)

        taiKhoan.trangThai = TrangThaiTaiKhoan.VO_HIEU_HOA
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Loi khi vo hieu hoa tai khoan id=%s", taiKhoanId)
            raise

        # R10.8: revoke every active session → old tokens are rejected immediately (401).
        soThuHoi = revokeAllSessions(self.db, taiKhoanId)
        logger.info(
            "Vo hieu hoa tai khoan thanh cong: admin id=%s, taiKhoanId=%s, thu hoi %d phien.",
            admin.id,
            taiKhoanId,
            soThuHoi,
        )

    # --- R10.3: re-enable ---------------------------------------------------
    def enableAccount(self, admin: TaiKhoan, taiKhoanId: str) -> None:
        """Re-enable a disabled account (R10.3) — QUAN_TRI only.

        Target account does not exist → `NotFoundError` (R10.5). Valid → set the state
        to HOAT_DONG.
        """
        self._yeuCauQuanTri(admin)
        taiKhoan = self._layTaiKhoanDich(taiKhoanId)

        taiKhoan.trangThai = TrangThaiTaiKhoan.HOAT_DONG
        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Loi khi kich hoat lai tai khoan id=%s", taiKhoanId)
            raise

        logger.info(
            "Kich hoat lai tai khoan thanh cong: admin id=%s, taiKhoanId=%s.",
            admin.id,
            taiKhoanId,
        )
