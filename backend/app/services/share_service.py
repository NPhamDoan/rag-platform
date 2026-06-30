"""ShareService + the `resolveAccess` authorization model (task 4.1).

Implements the central "Authorization model" from design.md: a single function
`resolveAccess(db, taiKhoan, khongGian)` that decides an account's access level to a
document workspace. Every layer (api dependencies, services, pipelines) relies on this
result to enforce permissions CONSISTENTLY (R3.2, R3.3).

Conventions:
- `MucTruyCap` is an ascending-ordered `IntEnum` (NONE < CHI_DOC < GHI < CHU_SO_HUU)
  → `>=` comparison is used to check "minimum required permission" (e.g. a write
  operation needs `>= GHI`).
- Naming: entity/field in Vietnamese without diacritics; verb/method in English.
- Logging goes through the central logger; errors are NEVER swallowed silently.

`ShareService` provides `resolveAccess` together with `grantShare`/`revokeShare` (R11):
sharing and revoking workspace access, both requiring `CHU_SO_HUU` (R11.7).
"""

from __future__ import annotations

import enum
import logging

from sqlalchemy.orm import Session

from app.db.models import ChiaSe, KhongGianTaiLieu, MucQuyen, TaiKhoan
from app.errors import AuthorizationError, NotFoundError, ValidationError

logger = logging.getLogger(__name__)

# Set of valid permission levels when sharing (R11.4) — only CHI_DOC | GHI.
_MUC_QUYEN_HOP_LE = (MucQuyen.CHI_DOC, MucQuyen.GHI)

# Shared error messages for grant/revoke.
_KHONG_GIAN_KHONG_TON_TAI = "Khong tim thay khong gian tai lieu."
_KHONG_PHAI_CHU_SO_HUU = "Chi chu so huu moi duoc chia se hoac thu hoi quyen."
_MUC_QUYEN_NGOAI_TAP = "mucQuyen chia se phai la CHI_DOC hoac GHI."
_TAI_KHOAN_DICH_KHONG_TON_TAI = "Khong tim thay tai khoan dich de chia se."
_KHONG_TU_CHIA_SE = "Khong the chia se khong gian cho chinh chu so huu."


class MucTruyCap(enum.IntEnum):
    """The access level an account has to a workspace (ascending order).

    The order (numeric value) allows `>=` comparison to check the minimum required
    permission: NONE(0) < CHI_DOC(1) < GHI(2) < CHU_SO_HUU(3). Reading/querying needs
    `>= CHI_DOC`; write operations need `>= GHI`; sharing/deleting/renaming a workspace
    needs `CHU_SO_HUU`.
    """

    NONE = 0
    CHI_DOC = 1
    GHI = 2
    CHU_SO_HUU = 3


# Mapping from MucQuyen (ChiaSe record) → the corresponding MucTruyCap.
_MUC_QUYEN_SANG_TRUY_CAP: dict[MucQuyen, MucTruyCap] = {
    MucQuyen.CHI_DOC: MucTruyCap.CHI_DOC,
    MucQuyen.GHI: MucTruyCap.GHI,
}


def resolveAccess(
    db: Session, taiKhoan: TaiKhoan, khongGian: KhongGianTaiLieu
) -> MucTruyCap:
    """Compute `taiKhoan`'s access level to `khongGian` (R3.2, R3.3, R11).

    Rules (per design.md "Authorization model"):
    1. Owner (`khongGian.chuSoHuuId == taiKhoan.id`) → `CHU_SO_HUU`.
    2. A `ChiaSe` record exists for the pair (khongGianId, taiKhoanId) → the
       corresponding `mucQuyen` (CHI_DOC | GHI).
    3. Otherwise → `NONE` (every operation gets 403/404).
    """
    if khongGian.chuSoHuuId == taiKhoan.id:
        return MucTruyCap.CHU_SO_HUU

    chiaSe = (
        db.query(ChiaSe)
        .filter(
            ChiaSe.khongGianId == khongGian.id,
            ChiaSe.taiKhoanId == taiKhoan.id,
        )
        .first()
    )
    if chiaSe is not None:
        return _MUC_QUYEN_SANG_TRUY_CAP[chiaSe.mucQuyen]

    return MucTruyCap.NONE


class ShareService:
    """Workspace sharing service operating on a single SQLAlchemy `Session`.

    Provides `resolveAccess` (the central authorization model) together with
    `grantShare` / `revokeShare` (R11). Every share/revoke operation requires
    `CHU_SO_HUU` (R11.7).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    def resolveAccess(
        self, taiKhoan: TaiKhoan, khongGian: KhongGianTaiLieu
    ) -> MucTruyCap:
        """Delegate to the module-level `resolveAccess` function (a single source of truth)."""
        return resolveAccess(self.db, taiKhoan, khongGian)

    # --- Owner permission check --------------------------------------------
    def _layKhongGianCuaChuSoHuu(
        self, chuSoHuu: TaiKhoan, khongGianId: str
    ) -> KhongGianTaiLieu:
        """Fetch the workspace + ensure `chuSoHuu` is the CHU_SO_HUU (R11.7).

        Does not exist → `NotFoundError` (404); exists but not the owner →
        `AuthorizationError` (403). Permission is checked BEFORE validating the other
        parameters so no information is leaked to an unauthorized user.
        """
        khongGian = self.db.get(KhongGianTaiLieu, khongGianId)
        if khongGian is None:
            logger.info("Khong tim thay khong gian khi chia se: id=%s", khongGianId)
            raise NotFoundError(_KHONG_GIAN_KHONG_TON_TAI)

        if resolveAccess(self.db, chuSoHuu, khongGian) != MucTruyCap.CHU_SO_HUU:
            logger.info(
                "Tu choi chia se: tai khoan id=%s khong phai chu so huu khong gian id=%s",
                chuSoHuu.id,
                khongGianId,
            )
            raise AuthorizationError(_KHONG_PHAI_CHU_SO_HUU)
        return khongGian

    # --- Share / revoke -----------------------------------------------------
    def grantShare(
        self,
        chuSoHuu: TaiKhoan,
        khongGianId: str,
        taiKhoanMucTieuId: str,
        mucQuyen: MucQuyen,
    ) -> ChiaSe:
        """Grant another account access to a workspace (R11.1, R11.4-11.7).

        Check order:
        1. Workspace exists + `chuSoHuu` is the CHU_SO_HUU (404 / 403, R11.7).
        2. `mucQuyen` ∈ {CHI_DOC, GHI}; outside the set → `ValidationError` (400, R11.4).
        3. Target account exists; if not → `NotFoundError` (404, R11.5).
        4. Cannot share with the owner itself (the owner already has full permission)
           → `ValidationError` (400). This decision avoids creating a redundant ChiaSe
           record, keeping `resolveAccess` always returning CHU_SO_HUU for the owner.

        Upsert: if a `ChiaSe` record already exists for the pair (khongGianId,
        taiKhoanId), update `mucQuyen` instead of violating the UNIQUE(khongGianId,
        taiKhoanId) constraint. Returns the `ChiaSe` record.
        """
        khongGian = self._layKhongGianCuaChuSoHuu(chuSoHuu, khongGianId)

        if mucQuyen not in _MUC_QUYEN_HOP_LE:
            logger.info(
                "Tu choi chia se: mucQuyen ngoai tap (khongGianId=%s, mucQuyen=%r)",
                khongGianId,
                mucQuyen,
            )
            raise ValidationError(_MUC_QUYEN_NGOAI_TAP)

        taiKhoanDich = self.db.get(TaiKhoan, taiKhoanMucTieuId)
        if taiKhoanDich is None:
            logger.info(
                "Tu choi chia se: tai khoan dich khong ton tai (id=%s)",
                taiKhoanMucTieuId,
            )
            raise NotFoundError(_TAI_KHOAN_DICH_KHONG_TON_TAI)

        if taiKhoanMucTieuId == chuSoHuu.id:
            logger.info(
                "Tu choi chia se: chu so huu tu chia se chinh minh (id=%s)",
                chuSoHuu.id,
            )
            raise ValidationError(_KHONG_TU_CHIA_SE)

        chiaSe = (
            self.db.query(ChiaSe)
            .filter(
                ChiaSe.khongGianId == khongGianId,
                ChiaSe.taiKhoanId == taiKhoanMucTieuId,
            )
            .first()
        )
        if chiaSe is None:
            chiaSe = ChiaSe(
                khongGianId=khongGianId,
                taiKhoanId=taiKhoanMucTieuId,
                mucQuyen=mucQuyen,
            )
            self.db.add(chiaSe)
            hanhDong = "tao moi"
        else:
            chiaSe.mucQuyen = mucQuyen
            hanhDong = "cap nhat"

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi chia se khong gian id=%s cho tai khoan id=%s",
                khongGianId,
                taiKhoanMucTieuId,
            )
            raise

        self.db.refresh(chiaSe)
        logger.info(
            "Chia se thanh cong (%s): khongGianId=%s, taiKhoanId=%s, mucQuyen=%s",
            hanhDong,
            khongGianId,
            taiKhoanMucTieuId,
            mucQuyen.value,
        )
        return chiaSe

    def revokeShare(
        self, chuSoHuu: TaiKhoan, khongGianId: str, taiKhoanMucTieuId: str
    ) -> None:
        """Revoke an account's workspace access (R11.6, R11.7).

        Only the CHU_SO_HUU may revoke (404 / 403). Deleting the `ChiaSe` record for the
        pair (khongGianId, taiKhoanId) → `resolveAccess` then returns `NONE` (access is
        403/404). Revoking a non-existent permission is IDEMPOTENT (no error): the end
        goal (the target account has no permission) is already achieved.
        """
        self._layKhongGianCuaChuSoHuu(chuSoHuu, khongGianId)

        chiaSe = (
            self.db.query(ChiaSe)
            .filter(
                ChiaSe.khongGianId == khongGianId,
                ChiaSe.taiKhoanId == taiKhoanMucTieuId,
            )
            .first()
        )
        if chiaSe is None:
            logger.info(
                "Thu hoi quyen idempotent: khong co ban ghi ChiaSe (khongGianId=%s, taiKhoanId=%s)",
                khongGianId,
                taiKhoanMucTieuId,
            )
            return

        try:
            self.db.delete(chiaSe)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi thu hoi quyen khong gian id=%s cua tai khoan id=%s",
                khongGianId,
                taiKhoanMucTieuId,
            )
            raise

        logger.info(
            "Thu hoi quyen thanh cong: khongGianId=%s, taiKhoanId=%s",
            khongGianId,
            taiKhoanMucTieuId,
        )
