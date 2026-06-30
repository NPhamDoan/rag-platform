"""QuotaService — ATOMIC resource quota check + reservation (task 6.1).

Implements the signatures in design.md (QuotaService section):

    class QuotaService:
        def checkAndReserve(self, taiKhoanId, loai, luong, khongGianId=None) -> None: ...
        def releaseQuota(self, taiKhoanId, loai, luong, khongGianId=None) -> None: ...
        def setQuota(self, admin, taiKhoanId, hanMuc: HanMucInput) -> HanMuc: ...

Three resource types (`LoaiTaiNguyen`):
- `SO_KHONG_GIAN`  : number of KhongGianTaiLieu per TaiKhoan (R12.1).
- `DUNG_LUONG`     : total bytes (sum of TaiLieu.kichThuoc) per TaiKhoan (R12.2).
- `SO_TAI_LIEU`    : number of TaiLieu in ONE KhongGianTaiLieu (R12.3) — needs `khongGianId`.

ATOMIC check principle (R12.7): within a single transaction, lock the account's
`HanMuc` row (`with_for_update` — a no-op on SQLite, a real lock on Postgres) before
computing the current usage from LIVE DATA (count/sum of the actual rows). As a result,
two concurrent operations cannot both exceed the limit.

BOUNDARY check (R12.4): `mucHienTai + luong <= gioiHan` is ALLOWED (landing exactly on
the limit is valid); `> gioiHan` is rejected with `QuotaExceededError` (R12.1-12.3).

`releaseQuota`: usage is always derived from the actual rows (count/sum) rather than a
separate reservation state, so `releaseQuota` is a NO-OP (it only logs). The method is
kept for API contract completeness: the caller deleting the real resource (e.g.
deleting a TaiLieu) automatically reduces usage on the next `checkAndReserve`.

`setQuota`: updates an account's `HanMuc` (R12.5). Valid ranges are enforced by the
`HanMucInput` DTO (R12.6 — out of range → `ValidationError` as soon as the DTO is used);
target account does not exist → `NotFoundError`. The QUAN_TRI permission is enforced at
the route layer; the service receives already-validated values.

Logging goes through the central logger; errors are NEVER swallowed silently.
"""

from __future__ import annotations

import enum
import logging

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import HanMuc, KhongGianTaiLieu, TaiKhoan, TaiLieu
from app.errors import NotFoundError, QuotaExceededError, ValidationError
from app.models.schemas import HanMucInput

logger = logging.getLogger(__name__)

_TAI_KHOAN_KHONG_TON_TAI = "Khong tim thay tai khoan dich de cau hinh han muc."
_THIEU_KHONG_GIAN_ID = (
    "checkAndReserve loai SO_TAI_LIEU yeu cau khongGianId (so tai lieu tinh theo khong gian)."
)


class LoaiTaiNguyen(str, enum.Enum):
    """Resource type subject to quota (R12).

    - SO_KHONG_GIAN: number of KhongGianTaiLieu per TaiKhoan.
    - DUNG_LUONG: total capacity (bytes) per TaiKhoan.
    - SO_TAI_LIEU: number of TaiLieu in one KhongGianTaiLieu.
    """

    SO_KHONG_GIAN = "SO_KHONG_GIAN"
    DUNG_LUONG = "DUNG_LUONG"
    SO_TAI_LIEU = "SO_TAI_LIEU"


_QUOTA_ERROR_MESSAGES = {
    LoaiTaiNguyen.SO_KHONG_GIAN: "Da dat han muc so khong gian tai lieu toi da.",
    LoaiTaiNguyen.DUNG_LUONG: "Da dat han muc dung luong toi da.",
    LoaiTaiNguyen.SO_TAI_LIEU: "Da dat han muc so tai lieu toi da trong khong gian.",
}


class QuotaService:
    """Resource quota check + enforcement service, operating on a single `Session`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Internal helpers --------------------------------------------------
    def _layHanMucCoKhoa(self, taiKhoanId: str) -> HanMuc | None:
        """Fetch the account's `HanMuc`, locking the row for an atomic check (R12.7).

        `with_for_update` is a no-op on SQLite (safely ignored) and a real row lock on
        Postgres — so within a transaction two concurrent operations serialize at this
        point. Returns `None` if there is no HanMuc record yet (use config defaults).
        """
        return (
            self.db.query(HanMuc)
            .filter(HanMuc.taiKhoanId == taiKhoanId)
            .with_for_update()
            .first()
        )

    def _gioiHan(
        self, loai: LoaiTaiNguyen, hanMuc: HanMuc | None
    ) -> int:
        """Return the limit for `loai` from HanMuc (or the config default if absent)."""
        settings = get_settings()
        if loai is LoaiTaiNguyen.SO_KHONG_GIAN:
            return (
                hanMuc.soKhongGianToiDa
                if hanMuc is not None
                else settings.quota_so_khong_gian
            )
        if loai is LoaiTaiNguyen.DUNG_LUONG:
            return (
                hanMuc.dungLuongToiDa
                if hanMuc is not None
                else settings.quota_dung_luong
            )
        # SO_TAI_LIEU
        return (
            hanMuc.soTaiLieuToiDaMoiKhongGian
            if hanMuc is not None
            else settings.quota_so_tai_lieu
        )

    def _mucHienTai(
        self, taiKhoanId: str, loai: LoaiTaiNguyen, khongGianId: str | None
    ) -> int:
        """Compute current usage from LIVE DATA (count/sum of the actual rows)."""
        if loai is LoaiTaiNguyen.SO_KHONG_GIAN:
            return (
                self.db.query(KhongGianTaiLieu)
                .filter(KhongGianTaiLieu.chuSoHuuId == taiKhoanId)
                .count()
            )
        if loai is LoaiTaiNguyen.DUNG_LUONG:
            # Total bytes of every TaiLieu in the workspaces the account owns.
            return int(
                self.db.query(func.coalesce(func.sum(TaiLieu.kichThuoc), 0))
                .join(
                    KhongGianTaiLieu,
                    KhongGianTaiLieu.id == TaiLieu.khongGianId,
                )
                .filter(KhongGianTaiLieu.chuSoHuuId == taiKhoanId)
                .scalar()
                or 0
            )
        # SO_TAI_LIEU: number of TaiLieu in one workspace.
        return (
            self.db.query(TaiLieu)
            .filter(TaiLieu.khongGianId == khongGianId)
            .count()
        )

    # --- Check + reserve ---------------------------------------------------
    def checkAndReserve(
        self,
        taiKhoanId: str,
        loai: LoaiTaiNguyen,
        luong: int,
        khongGianId: str | None = None,
    ) -> None:
        """ATOMIC check before consuming `luong` of a resource (R12.1-12.4, 12.7).

        Within the current transaction: lock the account's HanMuc row, compute current
        usage from live data, then do the boundary check `mucHienTai + luong <= gioiHan`.
        Over the boundary → `QuotaExceededError` (nothing consumed). The method ONLY
        validates; creating the real resource is done by the caller within the SAME
        transaction.

        `luong` < 0 is invalid (e.g. negative) → `ValidationError`. The SO_TAI_LIEU type
        requires `khongGianId`.
        """
        if luong < 0:
            raise ValidationError("luong tai nguyen yeu cau khong duoc am.")
        if loai is LoaiTaiNguyen.SO_TAI_LIEU and not khongGianId:
            raise ValidationError(_THIEU_KHONG_GIAN_ID)

        hanMuc = self._layHanMucCoKhoa(taiKhoanId)
        gioiHan = self._gioiHan(loai, hanMuc)
        mucHienTai = self._mucHienTai(taiKhoanId, loai, khongGianId)

        if mucHienTai + luong > gioiHan:
            logger.info(
                "Tu choi cap phat han muc: taiKhoanId=%s, loai=%s, mucHienTai=%d, "
                "luong=%d, gioiHan=%d",
                taiKhoanId,
                loai.value,
                mucHienTai,
                luong,
                gioiHan,
            )
            raise QuotaExceededError(_QUOTA_ERROR_MESSAGES[loai])

        logger.info(
            "Cho phep cap phat han muc: taiKhoanId=%s, loai=%s, mucHienTai=%d, "
            "luong=%d, gioiHan=%d",
            taiKhoanId,
            loai.value,
            mucHienTai,
            luong,
            gioiHan,
        )

    def releaseQuota(
        self,
        taiKhoanId: str,
        loai: LoaiTaiNguyen,
        luong: int,
        khongGianId: str | None = None,
    ) -> None:
        """Release a reservation — NO-OP (usage is derived from live data).

        Since `checkAndReserve` computes usage from the actual rows (count/sum) rather
        than storing a reservation state, deleting the real resource (e.g. deleting a
        TaiLieu) automatically reduces usage on the next check. The method is kept for
        API contract completeness; it only logs for traceability.
        """
        logger.info(
            "releaseQuota (no-op): taiKhoanId=%s, loai=%s, luong=%d, khongGianId=%s",
            taiKhoanId,
            loai.value,
            luong,
            khongGianId,
        )

    # --- Quota configuration (admin) ---------------------------------------
    def setQuota(
        self, admin: TaiKhoan, taiKhoanId: str, hanMuc: HanMucInput
    ) -> HanMuc:
        """Update an account's HanMuc (R12.5, R12.6).

        Valid ranges are enforced by the `HanMucInput` DTO (R12.6 — out of range was
        already rejected when the DTO was used, keeping the existing HanMuc unchanged).
        Target account does not exist → `NotFoundError`. If there is no HanMuc record
        yet, create one from the config values. The QUAN_TRI permission is enforced at
        the route layer; the service receives already-validated values. Returns the
        updated HanMuc record.
        """
        taiKhoanDich = self.db.get(TaiKhoan, taiKhoanId)
        if taiKhoanDich is None:
            logger.info(
                "Tu choi cau hinh han muc: tai khoan dich khong ton tai (id=%s)",
                taiKhoanId,
            )
            raise NotFoundError(_TAI_KHOAN_KHONG_TON_TAI)

        banGhi = self.db.get(HanMuc, taiKhoanId)
        if banGhi is None:
            banGhi = HanMuc(taiKhoanId=taiKhoanId)
            self.db.add(banGhi)

        banGhi.soKhongGianToiDa = hanMuc.soKhongGianToiDa
        banGhi.dungLuongToiDa = hanMuc.dungLuongToiDa
        banGhi.soTaiLieuToiDaMoiKhongGian = hanMuc.soTaiLieuToiDaMoiKhongGian
        banGhi.tanSuatTruyVanMoiPhut = hanMuc.tanSuatTruyVanMoiPhut

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi cau hinh han muc cho tai khoan id=%s", taiKhoanId
            )
            raise

        self.db.refresh(banGhi)
        logger.info(
            "Cau hinh han muc thanh cong: admin=%s, taiKhoanId=%s, "
            "soKhongGian=%d, dungLuong=%d, soTaiLieu=%d, tanSuat=%d",
            admin.id,
            taiKhoanId,
            banGhi.soKhongGianToiDa,
            banGhi.dungLuongToiDa,
            banGhi.soTaiLieuToiDaMoiKhongGian,
            banGhi.tanSuatTruyVanMoiPhut,
        )
        return banGhi
