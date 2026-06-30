"""ConfigService — retrieval config + MauPrompt + operational limits (task 12.1, 12.3).

Implements the "Configuration & administration" section of design.md (ConfigService):

    class ConfigService:
        def updateRetrievalConfig(self, taiKhoan, khongGianId, cfg: RetrievalConfigInput) -> CauHinhTruyXuat: ...
            # R19: threshold ∈ [0,1], lower<=upper, valid k, valid weights; out of range → reject; requires WRITE.
        def resetRetrievalConfig(self, taiKhoan, khongGianId) -> CauHinhTruyXuat: ...  # R19.3
        def updatePromptTemplate(self, admin, vaiTro, noiDung) -> MauPrompt: ...
            # R20: QUAN_TRI only; applies INVARIANT_SAFETY_CONSTRAINTS that cannot be overridden.
        def resetPromptTemplate(self, admin, vaiTro) -> MauPrompt: ...  # R20.2
        def updateOperationalLimits(self, admin, limits: LimitsInput) -> LimitsInput: ...
            # R23: llm_timeout / sessionTtl / maxFileSize within range; applied at runtime, no code change.

MauPrompt principles (R20.1/20.2/20.3):
- Editing/resetting a MauPrompt is an ADMIN OPERATION → `VaiTro.QUAN_TRI` only; the
  NGUOI_DUNG role is rejected with `AuthorizationError` (403) and the MauPrompt is NOT
  changed.
- The `noiDung` the admin enters only acts as the BASE; the EFFECTIVE prompt always
  has `INVARIANT_SAFETY_CONSTRAINTS` appended (invariant, no MauPrompt can override it)
  via the `apply_invariant_safety` helper — consistent with Query_Pipeline.
  `effectivePrompt` returns the effective prompt so the safety constraint can be
  verified to always be present.
- `resetPromptTemplate` reverts `noiDung` to `DEFAULT_PROMPT_TEMPLATES[vaiTro]` (same
  default source as Query_Pipeline) and sets `isDefault=True`.

Operational limits principles (R23.1/23.2/23.3):
- QUAN_TRI only. Valid ranges are enforced by the `LimitsInput` DTO (llmTimeout ∈
  [LLM_TIMEOUT_MIN, MAX], sessionTtl ∈ [SESSION_TTL_MIN, MAX], maxFileSize ∈
  [MAX_FILE_SIZE_MB_MIN, MAX]). Out-of-range values are rejected by the DTO
  (`ValidationError`) BEFORE reaching the service → the current values are NOT
  partially modified (R23.2, atomic).
- Applied at runtime by updating the `Settings` singleton (`get_settings()`) — which is
  exactly the source the runtime reads (auth: `session_ttl_minutes`, document:
  `max_file_size_*`, query: `llm_timeout_seconds`), so the change takes effect WITHOUT
  any code change (R23.3).

General principles (R19):
- Valid ranges are enforced by the `RetrievalConfigInput` DTO (a single source of truth
  for rule R19.1: threshold ∈ [0,1], lower threshold <= upper threshold, k ∈
  [RETRIEVAL_K_MIN, MAX], weights ∈ [0,1]). Out-of-range values are rejected as soon as
  the DTO is used (pydantic `ValidationError`) BEFORE reaching the service — so the
  existing config is NEVER partially modified (R19.4: reject and keep unchanged).
- Editing/resetting the config is a WRITE operation → requires `resolveAccess >= GHI`;
  insufficient permission → `AuthorizationError` (403, R19.5) and NOTHING is changed.
  Permission is checked BEFORE any edit so no state is leaked to an unauthorized user.
- Workspace does not exist → `NotFoundError` (404).
- Updates are COMMITTED in a single transaction; the NEXT query against the workspace
  re-reads `CauHinhTruyXuat` (by khongGianId) from the DB, so it automatically uses the
  new value (R19.2).
- `resetRetrievalConfig` reverts to the default values taken from `app.config`
  (Settings) — the same source as the ORM defaults (0.3 / 0.5 / k=8 / 0.5 / 0.5)
  (R19.3).

Logging goes through the central logger; errors are NEVER swallowed silently.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import (
    CauHinhTruyXuat,
    KhongGianTaiLieu,
    MauPrompt,
    TaiKhoan,
    VaiTro,
)
from app.errors import AuthorizationError, NotFoundError, ValidationError
from app.models.schemas import LimitsInput, RetrievalConfigInput
from app.prompts.system_prompts import (
    DEFAULT_PROMPT_TEMPLATES,
    apply_invariant_safety,
)
from app.services.share_service import MucTruyCap, resolveAccess

logger = logging.getLogger(__name__)

_KHONG_GIAN_KHONG_TON_TAI = "Khong tim thay khong gian tai lieu."
_KHONG_DU_QUYEN_GHI = "Can quyen ghi khong gian de chinh cau hinh truy xuat."
_KHONG_DU_QUYEN_QUAN_TRI = "Can quyen QUAN_TRI de thuc hien thao tac quan tri nay."
_VAI_TRO_PROMPT_KHONG_HOP_LE = (
    "Vai tro MauPrompt khong hop le (chi: synthesis | verify | normalize)."
)
_NOI_DUNG_PROMPT_RONG = "Noi dung MauPrompt khong duoc rong."


class ConfigService:
    """Retrieval config (CauHinhTruyXuat) service, per workspace.

    Operates on a single SQLAlchemy `Session`. Editing/resetting the config requires
    WRITE permission on the workspace (R19.5).
    """

    def __init__(self, db: Session) -> None:
        self.db = db

    # --- Write permission check ---------------------------------------------
    def _layKhongGianCoQuyenGhi(
        self, taiKhoan: TaiKhoan, khongGianId: str
    ) -> KhongGianTaiLieu:
        """Fetch the workspace + ensure `taiKhoan` has WRITE permission (R19.5).

        Does not exist → `NotFoundError` (404); exists but insufficient write
        permission → `AuthorizationError` (403). Permission is checked BEFORE any edit
        so the existing config is kept unchanged.
        """
        khongGian = self.db.get(KhongGianTaiLieu, khongGianId)
        if khongGian is None:
            logger.info("Khong tim thay khong gian khi chinh cau hinh: id=%s", khongGianId)
            raise NotFoundError(_KHONG_GIAN_KHONG_TON_TAI)

        if resolveAccess(self.db, taiKhoan, khongGian) < MucTruyCap.GHI:
            logger.info(
                "Tu choi chinh cau hinh truy xuat: tai khoan id=%s khong co quyen ghi "
                "khong gian id=%s",
                taiKhoan.id,
                khongGianId,
            )
            raise AuthorizationError(_KHONG_DU_QUYEN_GHI)
        return khongGian

    def _layHoacTaoCauHinh(self, khongGianId: str) -> CauHinhTruyXuat:
        """Fetch the workspace's `CauHinhTruyXuat`, creating a default one if absent.

        A workspace created via `WorkspaceService.createWorkspace` always has a config
        record; this helper guards the case where it is missing (e.g. a workspace
        created directly) so nothing breaks.
        """
        cauHinh = self.db.get(CauHinhTruyXuat, khongGianId)
        if cauHinh is None:
            cauHinh = CauHinhTruyXuat(khongGianId=khongGianId)
            self.db.add(cauHinh)
        return cauHinh

    # --- Update / reset -----------------------------------------------------
    def updateRetrievalConfig(
        self, taiKhoan: TaiKhoan, khongGianId: str, cfg: RetrievalConfigInput
    ) -> CauHinhTruyXuat:
        """Update a workspace's CauHinhTruyXuat (R19.1, R19.2, R19.4, R19.5).

        `cfg` is a `RetrievalConfigInput` DTO that is ALREADY validated (threshold ∈
        [0,1], lower threshold <= upper threshold, valid k + weights) — out-of-range
        values were rejected by the DTO BEFORE reaching here, so there is no partial
        update (R19.4). Requires WRITE permission (R19.5). After commit, the next query
        against the workspace uses the new value (R19.2). Returns the updated config
        record.
        """
        self._layKhongGianCoQuyenGhi(taiKhoan, khongGianId)
        cauHinh = self._layHoacTaoCauHinh(khongGianId)

        cauHinh.nguongKhongTimThay = cfg.nguongKhongTimThay
        cauHinh.nguongDuLienQuan = cfg.nguongDuLienQuan
        cauHinh.k = cfg.k
        cauHinh.trongSoVector = cfg.trongSoVector
        cauHinh.trongSoBm25 = cfg.trongSoBm25

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi cap nhat cau hinh truy xuat khong gian id=%s", khongGianId
            )
            raise

        self.db.refresh(cauHinh)
        logger.info(
            "Cap nhat cau hinh truy xuat thanh cong: khongGianId=%s, nguongDuoi=%.4f, "
            "nguongTren=%.4f, k=%d, trongSoVector=%.4f, trongSoBm25=%.4f",
            khongGianId,
            cauHinh.nguongKhongTimThay,
            cauHinh.nguongDuLienQuan,
            cauHinh.k,
            cauHinh.trongSoVector,
            cauHinh.trongSoBm25,
        )
        return cauHinh

    def resetRetrievalConfig(
        self, taiKhoan: TaiKhoan, khongGianId: str
    ) -> CauHinhTruyXuat:
        """Reset CauHinhTruyXuat to default values (R19.3, R19.5).

        Requires WRITE permission. Default values are taken from `app.config` (Settings)
        — the same source as the ORM defaults (0.3 / 0.5 / k=8 / 0.5 / 0.5). After
        commit, the next query uses the default values. Returns the reset config record.
        """
        self._layKhongGianCoQuyenGhi(taiKhoan, khongGianId)
        cauHinh = self._layHoacTaoCauHinh(khongGianId)

        settings = get_settings()
        cauHinh.nguongKhongTimThay = settings.nguong_khong_tim_thay
        cauHinh.nguongDuLienQuan = settings.nguong_du_lien_quan
        cauHinh.k = settings.retrieval_k
        cauHinh.trongSoVector = settings.trong_so_vector
        cauHinh.trongSoBm25 = settings.trong_so_bm25

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "Loi khi dat lai cau hinh truy xuat khong gian id=%s", khongGianId
            )
            raise

        self.db.refresh(cauHinh)
        logger.info(
            "Dat lai cau hinh truy xuat ve mac dinh: khongGianId=%s, nguongDuoi=%.4f, "
            "nguongTren=%.4f, k=%d, trongSoVector=%.4f, trongSoBm25=%.4f",
            khongGianId,
            cauHinh.nguongKhongTimThay,
            cauHinh.nguongDuLienQuan,
            cauHinh.k,
            cauHinh.trongSoVector,
            cauHinh.trongSoBm25,
        )
        return cauHinh
    # --- Admin permission check (R20.4, R23.1) ------------------------------
    def _yeuCauQuanTri(self, admin: TaiKhoan) -> None:
        """Ensure `admin` has `VaiTro.QUAN_TRI`; otherwise → `AuthorizationError`.

        Permission is checked BEFORE changing any data so that NGUOI_DUNG is rejected
        (403) WITHOUT the MauPrompt / operational limits being changed (R20.4).
        """
        if admin.vaiTro != VaiTro.QUAN_TRI:
            logger.info(
                "Tu choi thao tac quan tri: tai khoan id=%s vai tro=%s, can QUAN_TRI.",
                admin.id,
                admin.vaiTro.value,
            )
            raise AuthorizationError(_KHONG_DU_QUYEN_QUAN_TRI)

    # --- MauPrompt (R20.1, R20.2, R20.3) ------------------------------------
    def updatePromptTemplate(
        self, admin: TaiKhoan, vaiTro: str, noiDung: str
    ) -> MauPrompt:
        """Edit a role's MauPrompt (R20.1) — QUAN_TRI only.

        `noiDung` (after strip) is the custom BASE the admin enters; the EFFECTIVE
        prompt always appends `INVARIANT_SAFETY_CONSTRAINTS` (see `effectivePrompt`),
        so the safety constraint CANNOT be overridden (R20.3). Invalid role or empty
        content → `ValidationError`; a non-QUAN_TRI role → `AuthorizationError`, keeping
        the MauPrompt unchanged (R20.4). Returns the saved MauPrompt record.
        """
        self._yeuCauQuanTri(admin)
        if vaiTro not in DEFAULT_PROMPT_TEMPLATES:
            logger.info("Tu choi chinh MauPrompt: vai tro khong hop le=%r", vaiTro)
            raise ValidationError(_VAI_TRO_PROMPT_KHONG_HOP_LE)

        base = (noiDung or "").strip()
        if not base:
            logger.info("Tu choi chinh MauPrompt: noi dung rong (vaiTro=%s)", vaiTro)
            raise ValidationError(_NOI_DUNG_PROMPT_RONG)

        mauPrompt = self.db.get(MauPrompt, vaiTro)
        if mauPrompt is None:
            mauPrompt = MauPrompt(vaiTro=vaiTro, noiDung=base, isDefault=False)
            self.db.add(mauPrompt)
        else:
            mauPrompt.noiDung = base
            mauPrompt.isDefault = False

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Loi khi chinh MauPrompt vai tro=%s", vaiTro)
            raise

        self.db.refresh(mauPrompt)
        logger.info(
            "Chinh MauPrompt thanh cong: vaiTro=%s, doDaiNoiDung=%d, isDefault=%s",
            vaiTro,
            len(mauPrompt.noiDung),
            mauPrompt.isDefault,
        )
        return mauPrompt

    def resetPromptTemplate(self, admin: TaiKhoan, vaiTro: str) -> MauPrompt:
        """Reset a role's MauPrompt to the default content (R20.2) — QUAN_TRI only.

        The default is taken from `DEFAULT_PROMPT_TEMPLATES` (same default source as
        Query_Pipeline); sets `isDefault=True`. Invalid role → `ValidationError`; a
        non-QUAN_TRI role → `AuthorizationError`. Returns the reset MauPrompt record.
        """
        self._yeuCauQuanTri(admin)
        if vaiTro not in DEFAULT_PROMPT_TEMPLATES:
            logger.info("Tu choi dat lai MauPrompt: vai tro khong hop le=%r", vaiTro)
            raise ValidationError(_VAI_TRO_PROMPT_KHONG_HOP_LE)

        macDinh = DEFAULT_PROMPT_TEMPLATES[vaiTro]
        mauPrompt = self.db.get(MauPrompt, vaiTro)
        if mauPrompt is None:
            mauPrompt = MauPrompt(vaiTro=vaiTro, noiDung=macDinh, isDefault=True)
            self.db.add(mauPrompt)
        else:
            mauPrompt.noiDung = macDinh
            mauPrompt.isDefault = True

        try:
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception("Loi khi dat lai MauPrompt vai tro=%s", vaiTro)
            raise

        self.db.refresh(mauPrompt)
        logger.info("Dat lai MauPrompt ve mac dinh: vaiTro=%s", vaiTro)
        return mauPrompt

    def effectivePrompt(self, vaiTro: str) -> str:
        """Return a role's EFFECTIVE prompt: base + INVARIANT_SAFETY_CONSTRAINTS.

        Base = `MauPrompt.noiDung` (stripped) if present, otherwise the role's default.
        Whatever base the admin sets, `INVARIANT_SAFETY_CONSTRAINTS` is always present
        and cannot be overridden (R20.3). Invalid role → `ValidationError`.
        """
        if vaiTro not in DEFAULT_PROMPT_TEMPLATES:
            raise ValidationError(_VAI_TRO_PROMPT_KHONG_HOP_LE)
        mauPrompt = self.db.get(MauPrompt, vaiTro)
        base = (mauPrompt.noiDung.strip() if mauPrompt else "") or DEFAULT_PROMPT_TEMPLATES[
            vaiTro
        ]
        return apply_invariant_safety(base)

    # --- Operational limits (R23.1, R23.2, R23.3) ---------------------------
    def updateOperationalLimits(
        self, admin: TaiKhoan, limits: LimitsInput
    ) -> LimitsInput:
        """Update operational limits and apply them at runtime (R23.1, R23.3) — QUAN_TRI only.

        `limits` is a `LimitsInput` DTO that is ALREADY validated (llmTimeout /
        sessionTtl / maxFileSize within range) — out-of-range values were rejected by
        the DTO (`ValidationError`) BEFORE reaching here, so the current values are NOT
        partially modified (R23.2, atomic). Applied by updating the `Settings` singleton
        — which is exactly the source the runtime reads (R23.3), so it takes effect
        without any code change. A non-QUAN_TRI role → `AuthorizationError`, keeping the
        values unchanged. Returns the applied values.
        """
        self._yeuCauQuanTri(admin)

        settings = get_settings()
        settings.llm_timeout_seconds = limits.llmTimeout
        settings.session_ttl_minutes = limits.sessionTtl
        settings.max_file_size_mb = limits.maxFileSize

        logger.info(
            "Cap nhat gioi han van hanh: llmTimeout=%ds, sessionTtl=%dphut, "
            "maxFileSize=%dMB (ap dung runtime)",
            limits.llmTimeout,
            limits.sessionTtl,
            limits.maxFileSize,
        )
        return limits
