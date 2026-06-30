"""Pydantic DTO schemas (request/response) for the Multi-User RAG Platform (task 2.2).

Declares every DTO exchanged over the REST API following the "Main DTOs (Pydantic,
models/schemas.py)" section in design.md. Naming follows the codebase convention:

- Vietnamese field names without diacritics (`tenDangNhap`, `nguongKhongTimThay`,
  `khoaChe`); names match the corresponding ORM fields exactly for easy mapping.
- A few composite fields follow the design exactly (`laFallback`, `laTongQuan`,
  `llmTimeout`).
- Enums are reused from `app.db.models` (MucQuyen, NhanXacMinh, TrangThaiTaiLieu).
- Valid ranges reuse the constants from `app.config` (thresholds, k, quota, limits)
  to stay in sync with the runtime ConfigService (task 12.x).

Pydantic v2: uses `Field` constraints, `field_validator`/`model_validator` for
field/model-level checks. Does NOT use `EmailStr` (the `email-validator` package is
not installed) — email format is validated with a simple regex per R1 (<=254 +
correct format).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.config import (
    LLM_TIMEOUT_MAX,
    LLM_TIMEOUT_MIN,
    MAX_FILE_SIZE_MB_MAX,
    MAX_FILE_SIZE_MB_MIN,
    NGUONG_MAX,
    NGUONG_MIN,
    QUOTA_DUNG_LUONG_MAX,
    QUOTA_DUNG_LUONG_MIN,
    QUOTA_SO_KHONG_GIAN_MAX,
    QUOTA_SO_KHONG_GIAN_MIN,
    QUOTA_SO_TAI_LIEU_MAX,
    QUOTA_SO_TAI_LIEU_MIN,
    RETRIEVAL_K_MAX,
    RETRIEVAL_K_MIN,
    SESSION_TTL_MAX,
    SESSION_TTL_MIN,
)
from app.db.models import (
    MucQuyen,
    NhanXacMinh,
    TrangThaiTaiKhoan,
    TrangThaiTaiLieu,
    VaiTro,
)

# Simple email regex (R1): characters before/after @ and a dot in the domain.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class _InputBase(BaseModel):
    """Base for input DTOs: forbid unknown fields, strip leading/trailing whitespace."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# --- Authentication / accounts (R1, R2, R25) -------------------------------
class RegisterInput(_InputBase):
    """Register a new account (R1)."""

    email: str = Field(..., max_length=254)
    tenDangNhap: str = Field(..., min_length=3, max_length=30)
    matKhau: str = Field(..., min_length=8, max_length=64)

    @field_validator("email")
    @classmethod
    def _kiem_email(cls, v: str) -> str:
        if not _EMAIL_RE.match(v):
            raise ValueError("email khong dung dinh dang")
        return v


class LoginInput(_InputBase):
    """Login (R2.1)."""

    tenDangNhap: str = Field(..., min_length=1, max_length=30)
    matKhau: str = Field(..., min_length=1, max_length=64)


class ChangePasswordInput(_InputBase):
    """Change password (R25.1)."""

    matKhauCu: str = Field(..., min_length=1, max_length=64)
    matKhauMoi: str = Field(..., min_length=8, max_length=64)


class ResetPasswordInput(_InputBase):
    """Reset password using a reset token (R25.4)."""

    tokenReset: str = Field(..., min_length=1)
    matKhauMoi: str = Field(..., min_length=8, max_length=64)


class ResetRequestInput(_InputBase):
    """Request a password reset by email (R25.2)."""

    email: str = Field(..., max_length=254)


class LoginResponse(BaseModel):
    """Login result (R2.1): session token + account role."""

    token: str
    vaiTro: VaiTro


class TokenResponse(BaseModel):
    """New session token after a refresh (R25.5)."""

    token: str


# --- Document workspaces & sharing (R4, R11) -------------------------------
class WorkspaceInput(_InputBase):
    """Create/edit a KhongGianTaiLieu (R4.1-4): ten 1-100, moTa <=1000."""

    ten: str = Field(..., min_length=1, max_length=100)
    moTa: str = Field(default="", max_length=1000)


class ShareInput(_InputBase):
    """Grant a share permission (R11): mucQuyen ∈ {CHI_DOC, GHI}."""

    taiKhoanMucTieuId: str = Field(..., min_length=1)
    mucQuyen: MucQuyen


class WorkspaceResponse(BaseModel):
    """Info about a KhongGianTaiLieu returned to the client (R3.1, R4) — mirrors the ORM."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ten: str
    moTa: str
    chuSoHuuId: str
    embeddingProvider: str
    collectionName: str
    createdAt: datetime


class ShareResponse(BaseModel):
    """A ChiaSe record returned after granting permission (R11) — mirrors the ORM."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    khongGianId: str
    taiKhoanId: str
    mucQuyen: MucQuyen


# --- Retrieval config (R6, R19) --------------------------------------------
class RetrievalConfigInput(_InputBase):
    """Retrieval config (R19): thresholds ∈ [0,1], lower<=upper, valid k + weights."""

    nguongKhongTimThay: float = Field(..., ge=NGUONG_MIN, le=NGUONG_MAX)
    nguongDuLienQuan: float = Field(..., ge=NGUONG_MIN, le=NGUONG_MAX)
    k: int = Field(..., ge=RETRIEVAL_K_MIN, le=RETRIEVAL_K_MAX)
    trongSoVector: float = Field(..., ge=0.0, le=1.0)
    trongSoBm25: float = Field(..., ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _kiem_nguong(self) -> "RetrievalConfigInput":
        # R19: the "not found" threshold (lower) must be <= the "relevant" threshold (upper)
        if self.nguongKhongTimThay > self.nguongDuLienQuan:
            raise ValueError(
                "nguongKhongTimThay phai <= nguongDuLienQuan"
            )
        return self


class RetrievalConfigResponse(BaseModel):
    """Retrieval config returned to the client (R19) — mirrors the CauHinhTruyXuat ORM."""

    model_config = ConfigDict(from_attributes=True)

    khongGianId: str
    nguongKhongTimThay: float
    nguongDuLienQuan: float
    k: int
    trongSoVector: float
    trongSoBm25: float


# --- Documents / chunk preview (R5, R18) -----------------------------------
class DocumentMetadataInput(_InputBase):
    """Metadata declared when uploading a TaiLieu (R5)."""

    tenFile: str = Field(..., min_length=1, max_length=255)
    dinhDang: str = Field(..., min_length=1, max_length=20)
    chienLuocChunk: str = Field(default="auto", max_length=50)


class ChunkPreview(BaseModel):
    """A Chunk in the preview (R18.1) — mirrors the Chunk ORM."""

    id: str
    thuTu: int = Field(..., ge=0)
    viTriBatDau: int = Field(..., ge=0)
    viTriKetThuc: int = Field(..., ge=0)
    noiDung: str


class PreviewResult(BaseModel):
    """Preview result after parse/chunk (R5.5, R18.1)."""

    soChunk: int = Field(..., ge=0)
    chunks: list[ChunkPreview] = Field(default_factory=list)


class IndexingResult(BaseModel):
    """Embedding commit result (R5.13) — the document moves to DA_EMBED."""

    taiLieuId: str
    soChunk: int = Field(..., ge=0)
    trangThai: TrangThaiTaiLieu


class DocumentSummary(BaseModel):
    """Summary of a TaiLieu in a paginated list (R5.7) — mirrors the TaiLieu ORM."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    tenFile: str
    dinhDang: str
    kichThuoc: int = Field(..., ge=0)
    trangThai: TrangThaiTaiLieu
    soChunk: int = Field(..., ge=0)
    createdAt: datetime


class PaginatedDocumentResponse(BaseModel):
    """Paginated list of TaiLieu with a total count (R5.7).

    `items` is the current page (at most `pageSize` elements); `tongSo` is the TOTAL
    number of TaiLieu in the workspace (not the count on this page) — to support
    pagination on the client side.
    """

    items: list[DocumentSummary] = Field(default_factory=list)
    tongSo: int = Field(..., ge=0)
    page: int = Field(..., ge=1)
    pageSize: int = Field(..., ge=1, le=100)


class ChunkEditOp(_InputBase):
    """Manual Chunk edit operation (R18.3): merge | split | adjust at `viTri`.

    - merge: merge the Chunk at `viTri` with the next Chunk.
    - split: split the Chunk at `viTri` at offset `viTriCat` (within the Chunk content).
    - adjust: adjust the Chunk boundary at `viTri` to [viTriBatDauMoi,
      viTriKetThucMoi].
    """

    loai: Literal["merge", "split", "adjust"]
    viTri: int = Field(..., ge=0)
    viTriCat: int | None = Field(default=None, ge=0)
    viTriBatDauMoi: int | None = Field(default=None, ge=0)
    viTriKetThucMoi: int | None = Field(default=None, ge=0)


class RechunkInput(_InputBase):
    """Optional parameters when re-chunking a document (R5.12, R18.7).

    Leaving both fields empty → keep the document's current strategy/parameters.
    `thamSo` (if present) is a dict {kichThuocMucTieu, doChongLan} — the pipeline
    normalizes it via `ChunkParams.from_any`.
    """

    chienLuocChunk: str | None = Field(default=None, max_length=50)
    thamSo: dict | None = None


# --- Queries & answer results (R6, R7, R8, R16) ----------------------------
class QueryInput(_InputBase):
    """Query (R6.3): cauHoi 1-1000; optional forced cheDo (R16.7-8)."""

    cauHoi: str = Field(..., min_length=1, max_length=1000)
    cheDo: Literal["tong-quan", "chi-tiet"] | None = None


class TrichDan(BaseModel):
    """Citation [n] <-> source Chunk (R7.5): marker in 1..N."""

    marker: int = Field(..., ge=1)
    chunkId: str
    taiLieuId: str
    noiDung: str


class KetQuaTraLoi(BaseModel):
    """Answer result (R7, R8, R16): with citations + verification label + fallback flag."""

    traLoi: str
    trichDan: list[TrichDan] = Field(default_factory=list)
    nhanXacMinh: NhanXacMinh
    laFallback: bool = False
    laTongQuan: bool = False


class HistoryItemResponse(BaseModel):
    """A LichSuTroChuyen item returned to the client (R9.3, R9.6) — mirrors the ORM.

    `nguonConKhaDung=False` when the source document has been re-chunked (R9.8); the
    client can flag that the cited source is no longer available.
    """

    model_config = ConfigDict(from_attributes=True)

    id: str
    cauHoi: str
    traLoi: str
    nhanXacMinh: NhanXacMinh
    nguonConKhaDung: bool
    createdAt: datetime


# --- API keys (BYOK) (R22) -------------------------------------------------
class KhoaApiInput(_InputBase):
    """Enter/update a KhoaApiNguoiDung (R22.1)."""

    providerTen: str = Field(..., min_length=1, max_length=50)
    vaiTro: str = Field(..., min_length=1, max_length=50)
    khoa: str = Field(..., min_length=1)


class KhoaApiMasked(BaseModel):
    """Display a KhoaApiNguoiDung in masked form (R22.3) — never leaks plaintext."""

    providerTen: str
    vaiTro: str
    khoaChe: str


# --- Quotas & operational limits (R12, R23) --------------------------------
class HanMucInput(_InputBase):
    """Configure per-TaiKhoan resource HanMuc (R12.5-6)."""

    soKhongGianToiDa: int = Field(
        ..., ge=QUOTA_SO_KHONG_GIAN_MIN, le=QUOTA_SO_KHONG_GIAN_MAX
    )
    dungLuongToiDa: int = Field(
        ..., ge=QUOTA_DUNG_LUONG_MIN, le=QUOTA_DUNG_LUONG_MAX
    )
    soTaiLieuToiDaMoiKhongGian: int = Field(
        ..., ge=QUOTA_SO_TAI_LIEU_MIN, le=QUOTA_SO_TAI_LIEU_MAX
    )
    tanSuatTruyVanMoiPhut: int = Field(..., ge=1)


class LimitsInput(_InputBase):
    """Configurable operational limits (R23): llmTimeout (seconds), sessionTtl
    (minutes), maxFileSize (MB)."""

    llmTimeout: int = Field(..., ge=LLM_TIMEOUT_MIN, le=LLM_TIMEOUT_MAX)
    sessionTtl: int = Field(..., ge=SESSION_TTL_MIN, le=SESSION_TTL_MAX)
    maxFileSize: int = Field(..., ge=MAX_FILE_SIZE_MB_MIN, le=MAX_FILE_SIZE_MB_MAX)


class HanMucResponse(BaseModel):
    """Resource HanMuc returned to the client (R12.5) — mirrors the HanMuc ORM."""

    model_config = ConfigDict(from_attributes=True)

    taiKhoanId: str
    soKhongGianToiDa: int
    dungLuongToiDa: int
    soTaiLieuToiDaMoiKhongGian: int
    tanSuatTruyVanMoiPhut: int


# --- Account administration (R10) ------------------------------------------
class AccountResponse(BaseModel):
    """Info about a TaiKhoan returned to QUAN_TRI (R10.1) — does NOT include the password hash."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    tenDangNhap: str
    vaiTro: VaiTro
    trangThai: TrangThaiTaiKhoan
    createdAt: datetime


# --- MauPrompt (R20) -------------------------------------------------------
class MauPromptInput(_InputBase):
    """Edit a role's MauPrompt (R20.1): base noiDung (>=1 character after strip)."""

    noiDung: str = Field(..., min_length=1)


class MauPromptResponse(BaseModel):
    """MauPrompt returned to the client (R20) — mirrors the MauPrompt ORM (base + isDefault)."""

    model_config = ConfigDict(from_attributes=True)

    vaiTro: str
    noiDung: str
    isDefault: bool
