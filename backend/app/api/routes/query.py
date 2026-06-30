"""Per-workspace question-answering query route (task 13.4).

Wires `POST /api/workspaces/{id}/query` to `QueryPipeline` + `HistoryService` per the
"API endpoints" table in design.md:

| Method & Path                       | Perm    | Description      |
|-------------------------------------|---------|------------------|
| POST /api/workspaces/{id}/query     | CHI_DOC | R6, R16 query    |

Principles:
- Rate limiting (R24.2): the `rate_limit_query` dependency runs BEFORE any processing,
  exceeding the quota → 429 (RateLimitError) when NO LLM has yet been called. This
  dependency also authenticates the token and returns the `TaiKhoan`.
- Workspace scope (path param `id` = khongGianId): needs at least CHI_DOC, enforced
  via `require_workspace_access(CHI_DOC)` (maps 404/403 consistently).
- There is NO single orchestration method in QueryPipeline — the route assembles the
  flow per design.md (the "Query processing flow" diagram): validate → resolveMode →
  (overview: answerOverview | detail: normalize → retrieve → gating → answerDetail).
  The two gated branches (KHONG_TIM_THAY / CHUA_DU_LIEN_QUAN) return a fixed response,
  do NOT call the synthesis LLM (R6.5/6.6).
- Save history (R9.2): a failed `saveTurn` (returns None) does NOT break the response
  — the answer is still returned to the user, only a warning is logged.
- Logs key events at INFO through the centralized logger; does NOT log the full
  question/answer content (only lengths / decisions).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_db,
    get_query_pipeline,
    require_workspace_access,
)
from app.api.middleware.rate_limit import rate_limit_query
from app.config import get_settings
from app.db.models import CauHinhTruyXuat, KhongGianTaiLieu, NhanXacMinh, TaiKhoan
from app.models.schemas import KetQuaTraLoi, QueryInput
from app.pipelines.query_pipeline import (
    CHE_DO_TONG_QUAN,
    QueryPipeline,
    TrangThaiTruyXuat,
)
from app.services.history_service import HistoryService
from app.services.share_service import MucTruyCap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["query"])

# Gated responses (R6.5/6.6) — do NOT call the synthesis LLM.
_KHONG_TIM_THAY = "Khong tim thay thong tin lien quan trong tai lieu cua khong gian nay."
_CHUA_DU_LIEN_QUAN = (
    "Chua du thong tin lien quan trong tai lieu de tra loi cau hoi nay."
)


def _loadConfig(db: Session, khongGianId: str) -> CauHinhTruyXuat:
    """Return the workspace's CauHinhTruyXuat; no record → default from config.

    When a workspace is created, a CauHinhTruyXuat record is created alongside it
    (workspace_service); here we defend defensively: if missing → use the default
    `settings` values (does not write to the db).
    """
    cfg = db.get(CauHinhTruyXuat, khongGianId)
    if cfg is not None:
        return cfg
    s = get_settings()
    logger.info(
        "Khong co CauHinhTruyXuat cho khong gian id=%s, dung mac dinh cau hinh",
        khongGianId,
    )
    return CauHinhTruyXuat(
        khongGianId=khongGianId,
        nguongKhongTimThay=s.nguong_khong_tim_thay,
        nguongDuLienQuan=s.nguong_du_lien_quan,
        k=s.retrieval_k,
        trongSoVector=s.trong_so_vector,
        trongSoBm25=s.trong_so_bm25,
    )


def _gatedKetQua(trangThai: TrangThaiTruyXuat) -> KetQuaTraLoi:
    """Build a KetQuaTraLoi for the two gated branches (do NOT call the synthesis LLM) — R6.5/6.6."""
    traLoi = (
        _KHONG_TIM_THAY
        if trangThai == TrangThaiTruyXuat.KHONG_TIM_THAY
        else _CHUA_DU_LIEN_QUAN
    )
    return KetQuaTraLoi(
        traLoi=traLoi,
        trichDan=[],
        nhanXacMinh=NhanXacMinh.CHUA_XAC_MINH,
        laFallback=False,
        laTongQuan=False,
    )


@router.post("/workspaces/{id}/query", response_model=KetQuaTraLoi)
def query_workspace(
    id: str,
    payload: QueryInput,
    taiKhoan: TaiKhoan = Depends(rate_limit_query),
    khongGian: KhongGianTaiLieu = Depends(
        require_workspace_access(MucTruyCap.CHI_DOC)
    ),
    db: Session = Depends(get_db),
    pipeline: QueryPipeline = Depends(get_query_pipeline),
) -> KetQuaTraLoi:
    """Question-answering query within the workspace (R6, R16) — needs CHI_DOC, applies rate limit.

    `rate_limit_query` has already authenticated the token + applied the rate limit
    (429 before any LLM call). `require_workspace_access(CHI_DOC)` loads the workspace
    + enforces read permission. Assembles the flow per design.md, saves one history
    turn then returns the `KetQuaTraLoi`. Domain errors (ValidationError 1..1000...)
    are left to propagate to the global error handler.
    """
    cauHoi = pipeline.validateQuestion(payload.cauHoi)
    cheDo = pipeline.resolveMode(cauHoi, payload.cheDo)

    if cheDo == CHE_DO_TONG_QUAN:
        ketQua = pipeline.answerOverview(khongGian, taiKhoan, cauHoi)
    else:
        cauHoiChuan = pipeline.normalizeQuestion(cauHoi)
        cfg = _loadConfig(db, khongGian.id)
        kqTruyXuat = pipeline.retrieve(khongGian, cauHoiChuan, cfg)
        if kqTruyXuat.trangThai == TrangThaiTruyXuat.DU_LIEN_QUAN:
            ketQua = pipeline.answerDetail(cauHoiChuan, kqTruyXuat.chunks)
        else:
            ketQua = _gatedKetQua(kqTruyXuat.trangThai)

    # R9.2: a failed history save does NOT break the response — still return the answer.
    lichSu = HistoryService(db).saveTurn(taiKhoan, khongGian, cauHoi, ketQua)
    if lichSu is None:
        logger.warning(
            "POST /api/workspaces/%s/query: luu lich su that bai, van tra cau tra loi "
            "(taiKhoan=%s)",
            id,
            taiKhoan.id,
        )

    logger.info(
        "POST /api/workspaces/%s/query: cheDo=%s, soTrichDan=%d, nhan=%s, fallback=%s",
        id,
        cheDo,
        len(ketQua.trichDan),
        ketQua.nhanXacMinh.value,
        ketQua.laFallback,
    )
    return ketQua
