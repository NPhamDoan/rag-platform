"""Document routes: upload (preview) / list / chunk / commit / rechunk / reset / delete
(task 13.3).

Wires the REST endpoints to `DocumentPipeline` per the "API endpoints" table in
design.md:

| Method & Path                              | Perm    | Description                 |
|--------------------------------------------|---------|-----------------------------|
| POST   /api/workspaces/{id}/documents      | GHI     | R5.1 upload (preview)       |
| GET    /api/workspaces/{id}/documents      | CHI_DOC | R5.7 pagination             |
| GET    /api/documents/{id}/chunks          | GHI     | R18.1 view preview chunks   |
| PUT    /api/documents/{id}/chunks          | GHI     | R18.3 manual chunk edit     |
| POST   /api/documents/{id}/commit          | GHI     | R5.13 commit embed → DA_EMBED |
| POST   /api/documents/{id}/rechunk         | GHI     | R5.12, R18.7 re-chunk       |
| POST   /api/documents/{id}/reset           | GHI     | R18.6 reset to default      |
| DELETE /api/documents/{id}                 | GHI     | R5.8 delete document        |

Principles:
- WORKSPACE-SCOPED routes (path param `id` = khongGianId): upload needs GHI, listing
  needs CHI_DOC — enforced via `require_workspace_access` (loads the workspace + maps
  404/403 consistently with the other routes).
- DOCUMENT-SCOPED routes (path param `id` = taiLieuId): the document's workspace is
  resolved INSIDE the pipeline (`_loadDocWithWriteAccess`/`resolveAccess`), so the
  route only needs `get_current_user` + lets the pipeline enforce GHI permission and
  map domain errors (404 when not found, 403 when lacking permission).
- Upload takes multipart/form-data (`UploadFile`); the 50MB limit is enforced by the
  pipeline/config. The preview → commit flow: upload returns a `PreviewResult` (not
  yet embedded), only commit writes to the Vector_Store.
- Logs key events at INFO through the centralized logger; does NOT log file/chunk content.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from sqlalchemy.orm import Session

from app.api.dependencies import (
    get_current_user,
    get_db,
    get_document_pipeline,
    require_workspace_access,
)
from app.db.models import KhongGianTaiLieu, TaiKhoan
from app.models.schemas import (
    ChunkEditOp,
    ChunkPreview,
    IndexingResult,
    PaginatedDocumentResponse,
    PreviewResult,
    RechunkInput,
)
from app.pipelines.document_pipeline import (
    PAGE_SIZE_MAC_DINH,
    DocumentPipeline,
)
from app.services.share_service import MucTruyCap

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["documents"])


def _dinhDangTuTen(tenFile: str) -> str:
    """Derive the format from the filename extension ("a.pdf" -> "pdf"); none -> empty string.

    The pipeline normalizes + checks the set of supported formats (txt/md/pdf); an
    empty string is rejected with a ValidationError (R5.4).
    """
    return tenFile.rsplit(".", 1)[-1] if "." in tenFile else ""


# --- Workspace scope: upload + list ----------------------------------------
@router.post(
    "/workspaces/{id}/documents",
    response_model=PreviewResult,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    id: str,
    file: UploadFile = File(...),
    chienLuocChunk: str = Form("auto"),
    khongGian: KhongGianTaiLieu = Depends(
        require_workspace_access(MucTruyCap.GHI)
    ),
    taiKhoan: TaiKhoan = Depends(get_current_user),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> PreviewResult:
    """Upload + parse + chunk a document, holding it in the pending-review state (R5.1).

    `require_workspace_access(GHI)` has already loaded the workspace + enforced write
    permission (404/403). Returns a `PreviewResult` (not yet embedded); the `commit`
    step finalizes the embedding after the user reviews it. File > 50MB / unsupported
    format / 0 chunks → ValidationError (400).
    """
    fileBytes = await file.read()
    tenFile = file.filename or "tai-lieu"
    dinhDang = _dinhDangTuTen(tenFile)
    ketQua = pipeline.uploadDocument(
        taiKhoan, khongGian, fileBytes, tenFile, dinhDang, chienLuocChunk
    )
    logger.info(
        "POST /api/workspaces/%s/documents thanh cong: tenFile=%s, soChunk=%d",
        id,
        tenFile,
        ketQua.soChunk,
    )
    return ketQua


@router.get(
    "/workspaces/{id}/documents",
    response_model=PaginatedDocumentResponse,
)
def list_documents(
    id: str,
    page: int = 1,
    pageSize: int = PAGE_SIZE_MAC_DINH,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    _khongGian: KhongGianTaiLieu = Depends(
        require_workspace_access(MucTruyCap.CHI_DOC)
    ),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> PaginatedDocumentResponse:
    """List a workspace's documents by page (R5.7) — needs CHI_DOC.

    `require_workspace_access(CHI_DOC)` loads the workspace + enforces read
    permission. The pipeline validates `page`/`pageSize` (page >= 1, pageSize 1..100)
    → out of range 400.
    """
    ketQua = pipeline.listDocuments(taiKhoan, id, page, pageSize)
    logger.info(
        "GET /api/workspaces/%s/documents: page=%d, pageSize=%d, tongSo=%d",
        id,
        page,
        pageSize,
        ketQua.tongSo,
    )
    return ketQua


# --- Document scope: chunk / commit / rechunk / reset / delete -------------
@router.get(
    "/documents/{id}/chunks",
    response_model=PreviewResult,
)
def get_chunks(
    id: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> PreviewResult:
    """View the document's current preview Chunks (R18.1) — needs GHI (checked in the pipeline)."""
    ketQua = pipeline.getChunks(taiKhoan, id)
    logger.info("GET /api/documents/%s/chunks: soChunk=%d", id, ketQua.soChunk)
    return ketQua


@router.put(
    "/documents/{id}/chunks",
    response_model=list[ChunkPreview],
)
def edit_chunks(
    id: str,
    ops: list[ChunkEditOp],
    taiKhoan: TaiKhoan = Depends(get_current_user),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> list[ChunkPreview]:
    """Manually edit Chunks: merge | split | adjust (R18.3) — needs GHI (checked in the pipeline).

    An operation that creates an empty chunk / an invalid position → ValidationError
    (400), keeping the old chunks. If the document is already DA_EMBED, a manual edit
    invalidates the embedding.
    """
    chunks = pipeline.editChunks(taiKhoan, id, ops)
    logger.info(
        "PUT /api/documents/%s/chunks thanh cong: soOp=%d, soChunk=%d",
        id,
        len(ops),
        len(chunks),
    )
    return chunks


@router.post(
    "/documents/{id}/commit",
    response_model=IndexingResult,
)
def commit_document(
    id: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> IndexingResult:
    """Commit the document's embedding → DA_EMBED + write to Vector_Store (R5.13) — needs GHI.

    The document must be in the pending-review state; any other state → ValidationError (400).
    """
    ketQua = pipeline.commitEmbedding(taiKhoan, id)
    logger.info(
        "POST /api/documents/%s/commit thanh cong: soChunk=%d, trangThai=%s",
        id,
        ketQua.soChunk,
        ketQua.trangThai.value,
    )
    return ketQua


@router.post(
    "/documents/{id}/rechunk",
    response_model=PreviewResult,
)
def rechunk_document(
    id: str,
    payload: RechunkInput | None = None,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> PreviewResult:
    """Re-chunk the document (R5.12, R18.7) — needs GHI; idempotent, replaces the old Chunks.

    Optional body: `{chienLuocChunk?, thamSo?}`. Leave empty → keep the current
    strategy/parameters. 0 chunks → ValidationError (400), keeping the old Chunks.
    """
    chienLuocChunk = payload.chienLuocChunk if payload else None
    thamSo = payload.thamSo if payload else None
    ketQua = pipeline.rechunk(taiKhoan, id, chienLuocChunk, thamSo)
    logger.info(
        "POST /api/documents/%s/rechunk thanh cong: soChunk=%d", id, ketQua.soChunk
    )
    return ketQua


@router.post(
    "/documents/{id}/reset",
    response_model=PreviewResult,
)
def reset_document(
    id: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> PreviewResult:
    """Reset the strategy/parameters to default + clear custom rules, then re-chunk (R18.6).

    Needs GHI (checked in the pipeline). Returns the `PreviewResult` of the default
    re-chunk.
    """
    ketQua = pipeline.resetToDefault(taiKhoan, id)
    logger.info(
        "POST /api/documents/%s/reset thanh cong: soChunk=%d", id, ketQua.soChunk
    )
    return ketQua


@router.delete(
    "/documents/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def delete_document(
    id: str,
    taiKhoan: TaiKhoan = Depends(get_current_user),
    pipeline: DocumentPipeline = Depends(get_document_pipeline),
) -> None:
    """Delete the document + Chunk/TomTat + vectors (R5.8) — needs GHI (checked in the pipeline).

    Document does not exist → NotFoundError (404), no change to the Vector_Store.
    """
    pipeline.deleteDocument(taiKhoan, id)
    logger.info("DELETE /api/documents/%s thanh cong.", id)
