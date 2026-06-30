/**
 * Shared TypeScript types mirroring the backend DTOs (`backend/app/models/schemas.py`)
 * and the ORM entities (`backend/app/db/models.py`).
 *
 * Conventions (steering):
 * - Field names: Vietnamese without diacritics, matching the backend field names
 *   exactly so a response can be consumed without remapping.
 * - Interface/type names and any verbs are in English.
 * - These are pure structural types — no dependency on DOM, node, axios, react or
 *   react-native, so the same definitions serve both Web and Mobile.
 */

export * from "./enums.js";

import type {
  MucQuyen,
  NhanXacMinh,
  TrangThaiTaiKhoan,
  TrangThaiTaiLieu,
  VaiTro,
} from "./enums.js";

// --- Authentication / accounts (R1, R2, R25) -------------------------------

/** Register a new account (R1). */
export interface RegisterInput {
  email: string;
  tenDangNhap: string;
  matKhau: string;
}

/** Login (R2.1). */
export interface LoginInput {
  tenDangNhap: string;
  matKhau: string;
}

/** Change password (R25.1). */
export interface ChangePasswordInput {
  matKhauCu: string;
  matKhauMoi: string;
}

/** Reset password using a reset token (R25.4). */
export interface ResetPasswordInput {
  tokenReset: string;
  matKhauMoi: string;
}

/** Request a password reset by email (R25.2). */
export interface ResetRequestInput {
  email: string;
}

/** Login result (R2.1): session token + account role. */
export interface LoginResponse {
  token: string;
  vaiTro: VaiTro;
}

/** New session token after a refresh (R25.5). */
export interface TokenResponse {
  token: string;
}

// --- Document workspaces & sharing (R3, R4, R11) ---------------------------

/** Create/edit a KhongGianTaiLieu (R4.1-4): ten 1-100, moTa <=1000. */
export interface WorkspaceInput {
  ten: string;
  moTa: string;
}

/** Grant a share permission (R11): mucQuyen in {CHI_DOC, GHI}. */
export interface ShareInput {
  taiKhoanMucTieuId: string;
  mucQuyen: MucQuyen;
}

/** A KhongGianTaiLieu returned to the client (R3.1, R4) — mirrors the ORM. */
export interface WorkspaceResponse {
  id: string;
  ten: string;
  moTa: string;
  chuSoHuuId: string;
  embeddingProvider: string;
  collectionName: string;
  /** ISO-8601 datetime string (serialized backend `createdAt`). */
  createdAt: string;
}

/** A ChiaSe record returned after granting permission (R11) — mirrors the ORM. */
export interface ShareResponse {
  id: string;
  khongGianId: string;
  taiKhoanId: string;
  mucQuyen: MucQuyen;
}

// --- Retrieval config (R6, R19) --------------------------------------------

/** Retrieval config (R19): thresholds in [0,1], lower<=upper, valid k + weights. */
export interface RetrievalConfigInput {
  nguongKhongTimThay: number;
  nguongDuLienQuan: number;
  k: number;
  trongSoVector: number;
  trongSoBm25: number;
}

/** Retrieval config returned to the client (R19) — mirrors the CauHinhTruyXuat ORM. */
export interface RetrievalConfigResponse {
  khongGianId: string;
  nguongKhongTimThay: number;
  nguongDuLienQuan: number;
  k: number;
  trongSoVector: number;
  trongSoBm25: number;
}

// --- Documents / chunk preview (R5, R18) -----------------------------------

/** Metadata declared when uploading a TaiLieu (R5). */
export interface DocumentMetadataInput {
  tenFile: string;
  dinhDang: string;
  chienLuocChunk: string;
}

/** A Chunk in the preview (R18.1) — mirrors the Chunk ORM. */
export interface ChunkPreview {
  id: string;
  thuTu: number;
  viTriBatDau: number;
  viTriKetThuc: number;
  noiDung: string;
}

/** Preview result after parse/chunk (R5.5, R18.1). */
export interface PreviewResult {
  soChunk: number;
  chunks: ChunkPreview[];
}

/** Embedding commit result (R5.13) — the document moves to DA_EMBED. */
export interface IndexingResult {
  taiLieuId: string;
  soChunk: number;
  trangThai: TrangThaiTaiLieu;
}

/** Summary of a TaiLieu in a paginated list (R5.7) — mirrors the TaiLieu ORM. */
export interface DocumentSummary {
  id: string;
  tenFile: string;
  dinhDang: string;
  kichThuoc: number;
  trangThai: TrangThaiTaiLieu;
  soChunk: number;
  /** ISO-8601 datetime string (serialized backend `createdAt`). */
  createdAt: string;
}

/** Paginated list of TaiLieu with a total count (R5.7). */
export interface PaginatedDocumentResponse {
  items: DocumentSummary[];
  tongSo: number;
  page: number;
  pageSize: number;
}

/** Manual Chunk edit operation (R18.3): merge | split | adjust at `viTri`. */
export interface ChunkEditOp {
  loai: "merge" | "split" | "adjust";
  viTri: number;
  viTriCat?: number | null;
  viTriBatDauMoi?: number | null;
  viTriKetThucMoi?: number | null;
}

/** Optional parameters when re-chunking a document (R5.12, R18.7). */
export interface RechunkInput {
  chienLuocChunk?: string | null;
  thamSo?: Record<string, unknown> | null;
}

// --- Queries & answer results (R6, R7, R8, R16) ----------------------------

/** Query (R6.3): cauHoi 1-1000; optional forced cheDo (R16.7-8). */
export interface QueryInput {
  cauHoi: string;
  cheDo?: "tong-quan" | "chi-tiet" | null;
}

/** Citation [n] <-> source Chunk (R7.5): marker in 1..N. */
export interface TrichDan {
  marker: number;
  chunkId: string;
  taiLieuId: string;
  noiDung: string;
}

/** Answer result (R7, R8, R16): citations + verification label + fallback flag. */
export interface KetQuaTraLoi {
  traLoi: string;
  trichDan: TrichDan[];
  nhanXacMinh: NhanXacMinh;
  laFallback: boolean;
  laTongQuan: boolean;
}

/** A LichSuTroChuyen item returned to the client (R9.3, R9.6) — mirrors the ORM. */
export interface HistoryItemResponse {
  id: string;
  cauHoi: string;
  traLoi: string;
  nhanXacMinh: NhanXacMinh;
  nguonConKhaDung: boolean;
  /** ISO-8601 datetime string (serialized backend `createdAt`). */
  createdAt: string;
}

// --- API keys (BYOK) (R22) -------------------------------------------------

/** Enter/update a KhoaApiNguoiDung (R22.1). */
export interface KhoaApiInput {
  providerTen: string;
  vaiTro: string;
  khoa: string;
}

/** Display a KhoaApiNguoiDung in masked form (R22.3) — never leaks plaintext. */
export interface KhoaApiMasked {
  providerTen: string;
  vaiTro: string;
  khoaChe: string;
}

// --- Quotas & operational limits (R12, R23) --------------------------------

/** Configure per-TaiKhoan resource HanMuc (R12.5-6). */
export interface HanMucInput {
  soKhongGianToiDa: number;
  dungLuongToiDa: number;
  soTaiLieuToiDaMoiKhongGian: number;
  tanSuatTruyVanMoiPhut: number;
}

/** Configurable operational limits (R23): seconds / minutes / MB. */
export interface LimitsInput {
  llmTimeout: number;
  sessionTtl: number;
  maxFileSize: number;
}

/** Resource HanMuc returned to the client (R12.5) — mirrors the HanMuc ORM. */
export interface HanMucResponse {
  taiKhoanId: string;
  soKhongGianToiDa: number;
  dungLuongToiDa: number;
  soTaiLieuToiDaMoiKhongGian: number;
  tanSuatTruyVanMoiPhut: number;
}

// --- Account administration (R10) ------------------------------------------

/** A TaiKhoan returned to QUAN_TRI (R10.1) — never includes the password hash. */
export interface AccountResponse {
  id: string;
  email: string;
  tenDangNhap: string;
  vaiTro: VaiTro;
  trangThai: TrangThaiTaiKhoan;
  /** ISO-8601 datetime string (serialized backend `createdAt`). */
  createdAt: string;
}

// --- MauPrompt (R20) -------------------------------------------------------

/** Edit a role's MauPrompt (R20.1): base noiDung (>=1 character after strip). */
export interface MauPromptInput {
  noiDung: string;
}

/** MauPrompt returned to the client (R20) — mirrors the MauPrompt ORM. */
export interface MauPromptResponse {
  vaiTro: string;
  noiDung: string;
  isDefault: boolean;
}

// --- Entity aliases requested by the task list -----------------------------
// The backend exposes these entities through the response DTOs above. The aliases
// give the client the Vietnamese entity names from the task list without
// duplicating the field definitions.

/** Document workspace entity, as seen by the client (alias of WorkspaceResponse). */
export type KhongGianTaiLieu = WorkspaceResponse;

/** Document entity, as seen by the client in listings (alias of DocumentSummary). */
export type TaiLieu = DocumentSummary;

// --- Client-side concepts (chat UI) ----------------------------------------
// Not backend DTOs, but kept here so Web and Mobile render chat identically.
// Field names stay consistent with the backend (cauHoi / ketQua holding KetQuaTraLoi).

/** Which side of the conversation a Message belongs to. */
export type LoaiTinNhan = "nguoiDung" | "troLy";

/**
 * A single chat message wrapper. A `nguoiDung` message carries the question
 * (`cauHoi`); a `troLy` message carries the backend `KetQuaTraLoi` (`ketQua`) once
 * resolved, or `dangTai`/`loi` while pending or on failure.
 */
export interface Message {
  id: string;
  loai: LoaiTinNhan;
  cauHoi?: string;
  ketQua?: KetQuaTraLoi;
  dangTai?: boolean;
  loi?: string;
}

/** Client-side document metadata managed before upload (alias of DocumentMetadataInput). */
export type DocumentMetadata = DocumentMetadataInput;
