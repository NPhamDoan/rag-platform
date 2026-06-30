"""Admin routes (task 13.5) — `/api/admin/*`, `VaiTro.QUAN_TRI` only.

Wires the admin operations to `AdminService`, `QuotaService`, `ConfigService` per
the "API endpoints" table in design.md:

| Method & Path                          | Permission| Description                 |
|----------------------------------------|-----------|-----------------------------|
| GET    /api/admin/users                | QUAN_TRI  | R10.1 list accounts         |
| POST   /api/admin/users/{id}/disable   | QUAN_TRI  | R10.2,4,8 disable            |
| POST   /api/admin/users/{id}/enable    | QUAN_TRI  | R10.3 re-enable             |
| PUT    /api/admin/users/{id}/quota     | QUAN_TRI  | R12.5 configure quota        |
| GET    /api/admin/prompts/{vaiTro}     | QUAN_TRI  | R20 read MauPrompt          |
| PUT    /api/admin/prompts/{vaiTro}     | QUAN_TRI  | R20.1 edit MauPrompt         |
| PUT    /api/admin/limits               | QUAN_TRI  | R23 operational limits       |

Principles:
- Every endpoint depends on `require_role(VaiTro.QUAN_TRI)` → a non-QUAN_TRI account
  is rejected with 403 RIGHT at the dependency layer, before touching the service
  (R10.6). `QuotaService.setQuota` does not self-check permissions (by design), so
  require_role here is the only enforcement layer for the quota endpoint — required.
- Request DTOs (`HanMucInput`, `MauPromptInput`, `LimitsInput`) already validate the
  value ranges; out-of-range values → 400 via the global error handler, without
  touching the service.
- Reading MauPrompt (GET): reads the `MauPrompt` ORM directly (same style as
  `get_retrieval_config` reading `CauHinhTruyXuat`); no record yet → returns the
  default content + isDefault=True. An invalid role → 404 (no disclosure). Editing /
  setting otherwise is handled by `ConfigService`.
- Domain errors (NotFoundError/ValidationError/AuthorizationError) bubble up to the
  global error handler; NOT caught and swallowed. Logs key events at INFO; does NOT
  log sensitive data.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db, require_role
from app.db.models import MauPrompt, TaiKhoan, VaiTro
from app.errors import NotFoundError
from app.models.schemas import (
    AccountResponse,
    HanMucInput,
    HanMucResponse,
    LimitsInput,
    MauPromptInput,
    MauPromptResponse,
)
from app.prompts.system_prompts import DEFAULT_PROMPT_TEMPLATES
from app.services.admin_service import AdminService
from app.services.config_service import ConfigService
from app.services.quota_service import QuotaService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

_VAI_TRO_PROMPT_KHONG_TON_TAI = (
    "Khong tim thay MauPrompt cho vai tro (chi: synthesis | verify | normalize)."
)


# --- Account management (R10) ----------------------------------------------
@router.get("/users", response_model=list[AccountResponse])
def list_users(
    admin: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    db: Session = Depends(get_db),
) -> list[TaiKhoan]:
    """List all accounts (R10.1) — QUAN_TRI only."""
    danhSach = AdminService(db).listAccounts(admin)
    logger.info(
        "GET /api/admin/users: admin=%s, so tai khoan=%d", admin.id, len(danhSach)
    )
    return danhSach


@router.post("/users/{id}/disable", status_code=status.HTTP_204_NO_CONTENT)
def disable_user(
    id: str,
    admin: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    db: Session = Depends(get_db),
) -> None:
    """Disable an account + revoke sessions (R10.2, R10.4, R10.8) — QUAN_TRI only.

    Disabling oneself → 400; account does not exist → 404 (checked in the service).
    """
    AdminService(db).disableAccount(admin, id)
    logger.info("POST /api/admin/users/%s/disable thanh cong (admin=%s).", id, admin.id)


@router.post("/users/{id}/enable", status_code=status.HTTP_204_NO_CONTENT)
def enable_user(
    id: str,
    admin: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    db: Session = Depends(get_db),
) -> None:
    """Re-enable a disabled account (R10.3) — QUAN_TRI only.

    Account does not exist → 404 (checked in the service).
    """
    AdminService(db).enableAccount(admin, id)
    logger.info("POST /api/admin/users/%s/enable thanh cong (admin=%s).", id, admin.id)


@router.put("/users/{id}/quota", response_model=HanMucResponse)
def set_user_quota(
    id: str,
    payload: HanMucInput,
    admin: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    db: Session = Depends(get_db),
):
    """Configure an account's resource HanMuc (R12.5, R12.6) — QUAN_TRI only.

    The `HanMucInput` DTO already validates value ranges (out of range → 400). The
    target account does not exist → 404 (checked in the service). `QuotaService` does
    not self-check permissions, so require_role is the only QUAN_TRI enforcement layer
    for this endpoint.
    """
    hanMuc = QuotaService(db).setQuota(admin, id, payload)
    logger.info("PUT /api/admin/users/%s/quota thanh cong (admin=%s).", id, admin.id)
    return hanMuc


# --- MauPrompt (R20) -------------------------------------------------------
@router.get("/prompts/{vaiTro}", response_model=MauPromptResponse)
def get_prompt(
    vaiTro: str,
    admin: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    db: Session = Depends(get_db),
) -> MauPromptResponse:
    """Read a role's base MauPrompt (R20) — QUAN_TRI only.

    Invalid role → 404. No record yet (never edited) → returns the default content
    from `DEFAULT_PROMPT_TEMPLATES` with isDefault=True. Returns the BASE content (for
    the admin to edit); the immutable safety constraints are appended when the
    Query_Pipeline uses it, not shown here.
    """
    if vaiTro not in DEFAULT_PROMPT_TEMPLATES:
        logger.info("GET /api/admin/prompts: vai tro khong hop le=%r", vaiTro)
        raise NotFoundError(_VAI_TRO_PROMPT_KHONG_TON_TAI)

    mauPrompt = db.get(MauPrompt, vaiTro)
    if mauPrompt is None:
        logger.info("GET /api/admin/prompts/%s: tra mac dinh (chua chinh).", vaiTro)
        return MauPromptResponse(
            vaiTro=vaiTro,
            noiDung=DEFAULT_PROMPT_TEMPLATES[vaiTro],
            isDefault=True,
        )
    logger.info("GET /api/admin/prompts/%s thanh cong.", vaiTro)
    return MauPromptResponse.model_validate(mauPrompt)


@router.put("/prompts/{vaiTro}", response_model=MauPromptResponse)
def update_prompt(
    vaiTro: str,
    payload: MauPromptInput,
    admin: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    db: Session = Depends(get_db),
) -> MauPrompt:
    """Edit a role's base MauPrompt (R20.1) — QUAN_TRI only.

    Invalid role or empty content → 400 (checked in the service). The immutable
    safety constraints (`INVARIANT_SAFETY_CONSTRAINTS`) cannot be overwritten (R20.3).
    """
    mauPrompt = ConfigService(db).updatePromptTemplate(admin, vaiTro, payload.noiDung)
    logger.info("PUT /api/admin/prompts/%s thanh cong (admin=%s).", vaiTro, admin.id)
    return mauPrompt


# --- Operational limits (R23) ----------------------------------------------
@router.put("/limits", response_model=LimitsInput)
def update_limits(
    payload: LimitsInput,
    admin: TaiKhoan = Depends(require_role(VaiTro.QUAN_TRI)),
    db: Session = Depends(get_db),
) -> LimitsInput:
    """Update operational limits + apply them at runtime (R23.1-3) — QUAN_TRI only.

    The `LimitsInput` DTO already validates the ranges (out of range → 400, the
    current values are kept). `ConfigService` applies them to the Settings singleton
    (R23.3).
    """
    limits = ConfigService(db).updateOperationalLimits(admin, payload)
    logger.info(
        "PUT /api/admin/limits thanh cong (admin=%s): llmTimeout=%ds, "
        "sessionTtl=%dphut, maxFileSize=%dMB",
        admin.id,
        limits.llmTimeout,
        limits.sessionTtl,
        limits.maxFileSize,
    )
    return limits
