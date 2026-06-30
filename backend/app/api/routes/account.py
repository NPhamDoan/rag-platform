"""Account API key (BYOK) routes (task 13.5) — `/api/account/api-keys`.

Wires `ApiKeyService` to REST per the "API endpoints" table in design.md:

| Method & Path                  | Permission   | Description                    |
|--------------------------------|--------------|--------------------------------|
| GET    /api/account/api-keys   | authenticated| R22.3 list keys (MASKED)       |
| PUT    /api/account/api-keys   | authenticated| R22.1-2 set/update a key       |
| DELETE /api/account/api-keys   | authenticated| R22.1 delete a key (idempotent)|

Principles:
- Every endpoint depends on `get_current_user`; `ApiKeyService` is isolated by the
  calling account's `taiKhoanId` so another user's keys are never read/edited/deleted
  (R22.5).
- GET returns only the MASKED form (`****` + last 4 characters) — NEVER reveals
  plaintext (R22.3).
- PUT takes `KhoaApiInput` (providerTen, vaiTro, khoa); the key is Fernet-encrypted
  before storage (R22.2). Returns 204 (the client GETs to view the masked key).
- DELETE takes `providerTen` + `vaiTro` via query params (the endpoint has no path
  param); no record → skip (idempotent), returns 204.
- NEVER logs plaintext key values (R22.2, R22.3); logs key events at INFO through the
  centralized logger.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_current_user, get_db
from app.db.models import TaiKhoan
from app.models.schemas import KhoaApiInput, KhoaApiMasked
from app.services.api_key_service import ApiKeyService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/account", tags=["account"])


@router.get("/api-keys", response_model=list[KhoaApiMasked])
def list_api_keys(
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KhoaApiMasked]:
    """List the account's own API keys in MASKED form (R22.3, R22.5)."""
    danhSach = ApiKeyService(db).getMaskedKeys(taiKhoan)
    logger.info(
        "GET /api/account/api-keys: taiKhoan=%s, so khoa=%d",
        taiKhoan.id,
        len(danhSach),
    )
    return danhSach


@router.put("/api-keys", status_code=status.HTTP_204_NO_CONTENT)
def set_api_key(
    payload: KhoaApiInput,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Set/update the API key for (providerTen, vaiTro) (R22.1, R22.2).

    The key is Fernet-encrypted before storage; NEVER logs plaintext.
    """
    ApiKeyService(db).setApiKey(
        taiKhoan=taiKhoan,
        providerTen=payload.providerTen,
        vaiTro=payload.vaiTro,
        khoa=payload.khoa,
    )
    logger.info(
        "PUT /api/account/api-keys thanh cong: taiKhoan=%s, provider=%s, vaiTro=%s",
        taiKhoan.id,
        payload.providerTen,
        payload.vaiTro,
    )


@router.delete("/api-keys", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(
    providerTen: str = Query(..., min_length=1, max_length=50),
    vaiTro: str = Query(..., min_length=1, max_length=50),
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete the account's own API key (R22.1) — IDEMPOTENT (R22.5).

    No record → skip (still returns 204). Isolated by taiKhoanId so it cannot delete
    another account's key.
    """
    ApiKeyService(db).deleteApiKey(
        taiKhoan=taiKhoan, providerTen=providerTen, vaiTro=vaiTro
    )
    logger.info(
        "DELETE /api/account/api-keys thanh cong: taiKhoan=%s, provider=%s, vaiTro=%s",
        taiKhoan.id,
        providerTen,
        vaiTro,
    )
