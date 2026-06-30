"""Per-user chat history route (task 13.4).

Wires `HistoryService` to REST per the "API endpoints" table in design.md:

| Method & Path                       | Permission             | Description       |
|-------------------------------------|------------------------|-------------------|
| GET    /api/workspaces/{id}/history | CHI_DOC (own)          | R9.3 list         |
| DELETE /api/history/{lichSuId}      | owner of the item      | R9.6-7 delete     |

Principles:
- GET is workspace-scoped (path param `id` = khongGianId): needs at least CHI_DOC via
  `require_workspace_access(CHI_DOC)`; returns only the OWN history of `taiKhoan`
  within that workspace, <=50 items, newest first (fully isolated — R9.3/9.6).
- DELETE is history-item-scoped (path param `lichSuId`): only needs a token; the
  service deletes the item BELONGING to `taiKhoan`, a non-existent OR non-owned item
  → 404 (does not reveal the existence of another user's item — R9.7).
- Logs key events at INFO; does NOT log question/answer content.
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
from app.db.models import KhongGianTaiLieu, TaiKhoan
from app.models.schemas import HistoryItemResponse
from app.services.history_service import HistoryService
from app.services.share_service import MucTruyCap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["history"])


@router.get(
    "/workspaces/{id}/history",
    response_model=list[HistoryItemResponse],
)
def list_history(
    id: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    khongGian: KhongGianTaiLieu = Depends(
        require_workspace_access(MucTruyCap.CHI_DOC)
    ),
    db: Session = Depends(get_db),
) -> list[HistoryItemResponse]:
    """List your OWN history within the workspace (R9.3) — needs CHI_DOC.

    `require_workspace_access(CHI_DOC)` loads the workspace + enforces read
    permission. The service filters by `taiKhoanId` + `khongGianId`, sorts newest
    first, limits to 50.
    """
    muc = HistoryService(db).listHistory(taiKhoan, khongGian)
    logger.info(
        "GET /api/workspaces/%s/history: taiKhoan=%s, tra ve=%d muc",
        id,
        taiKhoan.id,
        len(muc),
    )
    return muc


@router.delete(
    "/history/{lichSuId}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_history_turn(
    lichSuId: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Delete a history item BELONGING to yourself (R9.6-7).

    Non-existent OR non-owned → NotFoundError (404) — does not reveal the existence of
    another user's item. The ORM cascade also deletes that item's TrichDan.
    """
    HistoryService(db).deleteTurn(taiKhoan, lichSuId)
    logger.info(
        "DELETE /api/history/%s thanh cong (taiKhoan=%s)", lichSuId, taiKhoan.id
    )
