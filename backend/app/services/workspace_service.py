"""WorkspaceService — KhongGianTaiLieu CRUD business logic (task 5.1).

Implements the signatures in design.md (WorkspaceService section):

    class WorkspaceService:
        def createWorkspace(self, chuSoHuu, ten, moTa="") -> KhongGianTaiLieu: ...
        def renameWorkspace(self, taiKhoan, khongGianId, tenMoi) -> KhongGianTaiLieu: ...
        def updateDescription(self, taiKhoan, khongGianId, moTa) -> KhongGianTaiLieu: ...
        def deleteWorkspace(self, taiKhoan, khongGianId) -> None: ...
        def listWorkspaces(self, taiKhoan) -> list[KhongGianTaiLieu]: ...

Principles:
- Validate ten/moTa via the `WorkspaceInput` DTO (a single source of truth for rules
  R4.1-4.4: ten 1-100 after trim, moTa <=1000) — pydantic errors are translated into a
  domain `ValidationError` that names the offending field.
- `createWorkspace` checks `HanMuc.soKhongGianToiDa` ATOMICally before creating: it
  counts the workspaces owned within the same transaction (locking the HanMuc row when
  the DB supports it — SQLite safely ignores `FOR UPDATE`); at/over the limit →
  `QuotaExceededError` (R12.1).
- Renaming / updating the description / deleting require `CHU_SO_HUU` (via
  `resolveAccess`); does not exist → `NotFoundError` (404); not the owner →
  `AuthorizationError` (403, R4.5).
- `deleteWorkspace` runs in ONE transaction: deleting the KhongGianTaiLieu cascades (ORM)
  to TaiLieu -> Chunk, ChiaSe, CauHinhTruyXuat, LichSuTroChuyen -> TrichDan; an error
  mid-way → rollback keeps the state unchanged (R4.6, R4.7, R4.8).
- `listWorkspaces` returns only OWNED or SHARED workspaces (R3.1) — does not leak
  others' workspaces.

Logging goes through the central logger; errors are NEVER swallowed silently.
"""

from __future__ import annotations

import logging

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import CauHinhTruyXuat, ChiaSe, HanMuc, KhongGianTaiLieu, TaiKhoan
from app.errors import (
    AuthorizationError,
    NotFoundError,
    QuotaExceededError,
    ValidationError,
)
from app.models.schemas import WorkspaceInput
from app.services.share_service import MucTruyCap, resolveAccess

logger = logging.getLogger(__name__)

# Per-field error messages (R4.2, R4.4) — used when the DTO reports an error.
_FIELD_ERROR_MESSAGES = {
    "ten": "ten khong gian phai tu 1 den 100 ky tu sau khi bo khoang trang.",
    "moTa": "moTa khong duoc dai qua 1000 ky tu.",
}

# Messages for operations that require owner permission (R4.5).
_NOT_OWNER_ERROR = "Chi chu so huu moi duoc thao tac tren khong gian nay."
_NOT_FOUND_ERROR = "Khong tim thay khong gian tai lieu."
_QUOTA_ERROR = "Da dat han muc so khong gian tai lieu toi da."


def _translateValidationError(exc: PydanticValidationError) -> ValidationError:
    """Translate a WorkspaceInput pydantic error into a domain ValidationError.

    Prefers the first error; a missing field → names the field, otherwise → a
    per-field message (R4.2/R4.4).
    """
    for loi in exc.errors():
        truong = loi["loc"][0] if loi["loc"] else ""
        if loi["type"] == "missing":
            return ValidationError(f"Thieu truong bat buoc: {truong}.")
        thongDiep = _FIELD_ERROR_MESSAGES.get(str(truong))
        if thongDiep:
            return ValidationError(thongDiep)
    return ValidationError("Du lieu khong gian tai lieu khong hop le.")


def _validateTen(ten: str) -> str:
    """Trim + check ten is 1-100 characters (R4.1, R4.2). Returns the trimmed ten."""
    try:
        dto = WorkspaceInput(ten=ten, moTa="")
    except PydanticValidationError as exc:
        raise _translateValidationError(exc) from exc
    return dto.ten


def _validateMoTa(moTa: str) -> str:
    """Trim + check moTa is <=1000 characters (R4.4). Returns the trimmed moTa.

    Uses a valid placeholder ten ("x") to check only the moTa constraint, keeping
    `WorkspaceInput` as the single source of truth for the rules.
    """
    try:
        dto = WorkspaceInput(ten="x", moTa=moTa)
    except PydanticValidationError as exc:
        raise _translateValidationError(exc) from exc
    return dto.moTa


class WorkspaceService:
    """KhongGianTaiLieu management service operating on a single SQLAlchemy `Session`."""

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Read/write with permission check ----------------------------------
    def _layKhongGianCuaChuSoHuu(
        self, taiKhoan: TaiKhoan, khongGianId: str
    ) -> KhongGianTaiLieu:
        """Fetch the workspace and ensure `taiKhoan` is the CHU_SO_HUU.

        Does not exist → `NotFoundError` (404); exists but not the owner →
        `AuthorizationError` (403, R4.5).
        """
        khongGian = self.db.get(KhongGianTaiLieu, khongGianId)
        if khongGian is None:
            logger.info("Khong tim thay khong gian: id=%s", khongGianId)
            raise NotFoundError(_NOT_FOUND_ERROR)

        if resolveAccess(self.db, taiKhoan, khongGian) != MucTruyCap.CHU_SO_HUU:
            logger.info(
                "Tu choi thao tac: tai khoan id=%s khong phai chu so huu khong gian id=%s",
                taiKhoan.id,
                khongGianId,
            )
            raise AuthorizationError(_NOT_OWNER_ERROR)
        return khongGian

    # --- CRUD --------------------------------------------------------------
    def createWorkspace(
        self, chuSoHuu: TaiKhoan, ten: str, moTa: str = ""
    ) -> KhongGianTaiLieu:
        """Create a new KhongGianTaiLieu for `chuSoHuu` (R4.1, R4.2, R12.1).

        Trim + check ten 1-100, moTa <=1000 (`ValidationError`). Before creating, check
        `HanMuc.soKhongGianToiDa` ATOMICally: count the workspaces owned within the same
        transaction (locking the HanMuc row when the DB supports it), at/over the limit →
        `QuotaExceededError`. Set `embeddingProvider` from the default config,
        `collectionName = f"ws_{id}"`, and create an accompanying default
        `CauHinhTruyXuat` (1-1 relationship) so later retrieval has a config available.
        """
        tenTrim = _validateTen(ten)
        moTaTrim = _validateMoTa(moTa)

        # Atomic quota check: lock the HanMuc row (no-op on SQLite) + count current.
        hanMuc = (
            self.db.query(HanMuc)
            .filter(HanMuc.taiKhoanId == chuSoHuu.id)
            .with_for_update()
            .first()
        )
        gioiHan = (
            hanMuc.soKhongGianToiDa
            if hanMuc is not None
            else get_settings().quota_so_khong_gian
        )
        soHienTai = (
            self.db.query(KhongGianTaiLieu)
            .filter(KhongGianTaiLieu.chuSoHuuId == chuSoHuu.id)
            .count()
        )
        if soHienTai >= gioiHan:
            logger.info(
                "Tu choi tao khong gian: dat han muc (id=%s, soHienTai=%d, gioiHan=%d)",
                chuSoHuu.id,
                soHienTai,
                gioiHan,
            )
            raise QuotaExceededError(_QUOTA_ERROR)

        khongGian = KhongGianTaiLieu(
            ten=tenTrim,
            moTa=moTaTrim,
            chuSoHuuId=chuSoHuu.id,
            embeddingProvider=get_settings().embedding_provider,
            # Temporary collectionName; reset by id after flush (= f"ws_{id}").
            collectionName="",
        )
        self.db.add(khongGian)
        self.db.flush()  # generate id to set collectionName + CauHinh foreign key.

        khongGian.collectionName = f"ws_{khongGian.id}"
        self.db.add(CauHinhTruyXuat(khongGianId=khongGian.id))

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Loi khi tao khong gian cho tai khoan id=%s", chuSoHuu.id)
            raise

        self.db.refresh(khongGian)
        logger.info(
            "Tao khong gian thanh cong: id=%s, chuSoHuu=%s, collection=%s",
            khongGian.id,
            chuSoHuu.id,
            khongGian.collectionName,
        )
        return khongGian

    def renameWorkspace(
        self, taiKhoan: TaiKhoan, khongGianId: str, tenMoi: str
    ) -> KhongGianTaiLieu:
        """Rename a workspace (R4.3, R4.5). Requires CHU_SO_HUU; checks ten 1-100."""
        khongGian = self._layKhongGianCuaChuSoHuu(taiKhoan, khongGianId)
        khongGian.ten = _validateTen(tenMoi)
        self.db.commit()
        self.db.refresh(khongGian)
        logger.info("Doi ten khong gian thanh cong: id=%s", khongGianId)
        return khongGian

    def updateDescription(
        self, taiKhoan: TaiKhoan, khongGianId: str, moTa: str
    ) -> KhongGianTaiLieu:
        """Update a workspace's description (R4.4, R4.5). Requires CHU_SO_HUU; moTa <=1000."""
        khongGian = self._layKhongGianCuaChuSoHuu(taiKhoan, khongGianId)
        khongGian.moTa = _validateMoTa(moTa)
        self.db.commit()
        self.db.refresh(khongGian)
        logger.info("Cap nhat mo ta khong gian thanh cong: id=%s", khongGianId)
        return khongGian

    def deleteWorkspace(self, taiKhoan: TaiKhoan, khongGianId: str) -> None:
        """Delete a workspace in ONE transaction (R4.6, R4.7, R4.8).

        Requires CHU_SO_HUU. Deleting `KhongGianTaiLieu` cascades (ORM) to: TaiLieu ->
        Chunk, ChiaSe, CauHinhTruyXuat, and LichSuTroChuyen -> TrichDan (the TrichDan
        referencing the workspace are removed, the workspace's chat history is also
        removed => no references remain to deleted Chunks). Any error mid-way → rollback
        to keep the state unchanged (R4.7).

        Note (R4.8): the current model sets `LichSuTroChuyen.khongGianId` NOT NULL +
        cascade delete, so history is deleted with the workspace (the strongest "no
        longer available" form) rather than kept and marked. The task 2.1 model is left
        unchanged; not modified here.
        """
        khongGian = self._layKhongGianCuaChuSoHuu(taiKhoan, khongGianId)
        try:
            self.db.delete(khongGian)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Loi khi xoa khong gian id=%s — da rollback", khongGianId)
            raise
        logger.info(
            "Xoa khong gian thanh cong: id=%s (cascade xoa TaiLieu/Chunk/TrichDan/LichSu).",
            khongGianId,
        )

    def listWorkspaces(self, taiKhoan: TaiKhoan) -> list[KhongGianTaiLieu]:
        """List OWNED or SHARED workspaces for `taiKhoan` (R3.1).

        Returns owned workspaces first, then the shared ones (deduplicated), NOT
        including other users' workspaces.
        """
        soHuu = (
            self.db.query(KhongGianTaiLieu)
            .filter(KhongGianTaiLieu.chuSoHuuId == taiKhoan.id)
            .all()
        )
        duocChiaSe = (
            self.db.query(KhongGianTaiLieu)
            .join(ChiaSe, ChiaSe.khongGianId == KhongGianTaiLieu.id)
            .filter(ChiaSe.taiKhoanId == taiKhoan.id)
            .all()
        )

        ketQua: list[KhongGianTaiLieu] = []
        daThay: set[str] = set()
        for kg in [*soHuu, *duocChiaSe]:
            if kg.id not in daThay:
                daThay.add(kg.id)
                ketQua.append(kg)

        logger.info(
            "Liet ke khong gian cho tai khoan id=%s: so huu=%d, duoc chia se=%d, tong=%d",
            taiKhoan.id,
            len(soHuu),
            len(duocChiaSe),
            len(ketQua),
        )
        return ketQua
