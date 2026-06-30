"""Document workspace + sharing + retrieval config routes (task 13.2).

Wires the REST endpoints to `WorkspaceService`, `ShareService`, `ConfigService` per
the "API endpoints" table in design.md:

| Method & Path                                       | Permission    | Description          |
|-----------------------------------------------------|---------------|----------------------|
| GET    /api/workspaces                              | authenticated | R3.1 (owned + shared) |
| POST   /api/workspaces                              | authenticated | R4.1                 |
| PATCH  /api/workspaces/{id}                         | owner         | R4.3-4               |
| DELETE /api/workspaces/{id}                         | owner         | R4.6-8               |
| POST   /api/workspaces/{id}/shares                  | owner         | R11.1                |
| DELETE /api/workspaces/{id}/shares/{taiKhoanId}     | owner         | R11.6                |
| GET    /api/workspaces/{id}/retrieval-config        | CHI_DOC       | R19 (read)           |
| PUT    /api/workspaces/{id}/retrieval-config        | GHI           | R19.1-5              |

Principles:
- Listing / creating a workspace only needs authentication (`get_current_user`) —
  each account lists its OWN OWNED + SHARED workspaces (R3.1).
- Reading the retrieval config needs CHI_DOC via `require_workspace_access` (path
  param `id`) — both loads the `KhongGianTaiLieu` and enforces read permission,
  mapping 404/403 consistently.
- The write/owner operations (PATCH/DELETE workspace, share/revoke, PUT config) let
  the SERVICE enforce permission (resolveAccess CHU_SO_HUU / >= GHI) → no double-check
  at the route layer; domain errors bubble up to the global error handler.
- Logs key events at INFO through the centralized logger; does NOT log sensitive data.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_user,
    get_db,
    require_workspace_access,
)
from app.db.models import CauHinhTruyXuat, KhongGianTaiLieu, TaiKhoan
from app.errors import NotFoundError
from app.models.schemas import (
    RetrievalConfigInput,
    RetrievalConfigResponse,
    ShareInput,
    ShareResponse,
    WorkspaceInput,
    WorkspaceResponse,
)
from app.services.config_service import ConfigService
from app.services.share_service import MucTruyCap, ShareService
from app.services.workspace_service import WorkspaceService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["workspaces"])

_CAU_HINH_KHONG_TON_TAI = "Khong tim thay cau hinh truy xuat cua khong gian."


# --- Workspace CRUD --------------------------------------------------------
@router.get("/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[KhongGianTaiLieu]:
    """List the current account's OWNED + SHARED workspaces (R3.1)."""
    danhSach = WorkspaceService(db).listWorkspaces(taiKhoan)
    logger.info(
        "GET /api/workspaces: taiKhoanId=%s, so khong gian=%d",
        taiKhoan.id,
        len(danhSach),
    )
    return danhSach


@router.post(
    "/workspaces",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace(
    payload: WorkspaceInput,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KhongGianTaiLieu:
    """Create a new workspace for the current account (R4.1, R4.2, R12.1)."""
    khongGian = WorkspaceService(db).createWorkspace(
        chuSoHuu=taiKhoan, ten=payload.ten, moTa=payload.moTa
    )
    logger.info(
        "POST /api/workspaces thanh cong: id=%s, chuSoHuu=%s",
        khongGian.id,
        taiKhoan.id,
    )
    return khongGian


@router.patch("/workspaces/{id}", response_model=WorkspaceResponse)
def update_workspace(
    id: str,
    payload: WorkspaceInput,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> KhongGianTaiLieu:
    """Rename + update the workspace description (R4.3, R4.4) — owner (checked in the service).

    Takes `WorkspaceInput` (ten required, moTa defaults to empty) and applies both
    fields. The service enforces CHU_SO_HUU permission: does not exist → 404, not the
    owner → 403.
    """
    service = WorkspaceService(db)
    service.renameWorkspace(taiKhoan, id, payload.ten)
    khongGian = service.updateDescription(taiKhoan, id, payload.moTa)
    logger.info("PATCH /api/workspaces/%s thanh cong.", id)
    return khongGian


@router.delete("/workspaces/{id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workspace(
    id: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete the workspace (cascade) — owner (checked in the service) (R4.6-8)."""
    WorkspaceService(db).deleteWorkspace(taiKhoan, id)
    logger.info("DELETE /api/workspaces/%s thanh cong.", id)


# --- Sharing / revoke ------------------------------------------------------
@router.post(
    "/workspaces/{id}/shares",
    response_model=ShareResponse,
    status_code=status.HTTP_201_CREATED,
)
def grant_share(
    id: str,
    payload: ShareInput,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShareResponse:
    """Grant another account access to the workspace (R11.1) — owner.

    The service enforces: workspace does not exist → 404, not the owner → 403,
    mucQuyen outside {CHI_DOC, GHI} → 400, target account does not exist → 404.
    """
    chiaSe = ShareService(db).grantShare(
        chuSoHuu=taiKhoan,
        khongGianId=id,
        taiKhoanMucTieuId=payload.taiKhoanMucTieuId,
        mucQuyen=payload.mucQuyen,
    )
    logger.info(
        "POST /api/workspaces/%s/shares thanh cong: taiKhoanMucTieu=%s, mucQuyen=%s",
        id,
        payload.taiKhoanMucTieuId,
        payload.mucQuyen.value,
    )
    return ShareResponse.model_validate(chiaSe)


@router.delete(
    "/workspaces/{id}/shares/{taiKhoanId}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_share(
    id: str,
    taiKhoanId: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Revoke an account's access (R11.6) — owner (idempotent)."""
    ShareService(db).revokeShare(
        chuSoHuu=taiKhoan, khongGianId=id, taiKhoanMucTieuId=taiKhoanId
    )
    logger.info(
        "DELETE /api/workspaces/%s/shares/%s thanh cong.", id, taiKhoanId
    )


# --- Retrieval config ------------------------------------------------------
@router.get(
    "/workspaces/{id}/retrieval-config",
    response_model=RetrievalConfigResponse,
)
def get_retrieval_config(
    id: str,
    khongGian: KhongGianTaiLieu = Depends(
        require_workspace_access(MucTruyCap.CHI_DOC)
    ),
    db: Session = Depends(get_db),
) -> CauHinhTruyXuat:
    """Read the workspace's retrieval config (R19) — needs CHI_DOC.

    `require_workspace_access` has already loaded the workspace + enforced read
    permission (404/403). The config is created alongside the workspace so it always
    exists; reads the ORM directly.
    """
    cauHinh = db.get(CauHinhTruyXuat, id)
    if cauHinh is None:
        logger.info("GET retrieval-config: thieu cau hinh khong gian id=%s", id)
        raise NotFoundError(_CAU_HINH_KHONG_TON_TAI)
    logger.info("GET /api/workspaces/%s/retrieval-config thanh cong.", id)
    return cauHinh


@router.put(
    "/workspaces/{id}/retrieval-config",
    response_model=RetrievalConfigResponse,
)
def update_retrieval_config(
    id: str,
    payload: RetrievalConfigInput,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CauHinhTruyXuat:
    """Update the retrieval config (R19.1-5) — needs GHI (checked in the service).

    The `RetrievalConfigInput` DTO already validates the value ranges
    (threshold/k/weights); the service enforces GHI permission: workspace does not
    exist → 404, insufficient permission → 403.
    """
    cauHinh = ConfigService(db).updateRetrievalConfig(taiKhoan, id, payload)
    logger.info("PUT /api/workspaces/%s/retrieval-config thanh cong.", id)
    return cauHinh
