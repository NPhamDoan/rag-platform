/**
 * Platform-agnostic API client factory for `@rag-platform/shared` (task 15.2).
 *
 * `createApiClient` is the single Hop_Dong_API_Dung_Chung (shared API contract)
 * consumed identically by the Web (React + Vite) and Mobile (React Native + Expo)
 * clients, so the same call produces the same behavior on both platforms (R29.1,
 * R29.2). It:
 *
 * - exposes EVERY backend endpoint (auth / workspaces / documents / query / history
 *   / admin / api-keys) as an English-verb method, using the Vietnamese-no-diacritics
 *   DTOs from `../types`;
 * - attaches `Authorization: Bearer <token>` whenever `getToken()` returns one — token
 *   STORAGE is the caller's concern (Web → localStorage, Mobile → expo-secure-store),
 *   so this package never touches any storage API (R29.1);
 * - on HTTP 401 invokes `onUnauthorized()` (so the caller can clear its token /
 *   redirect) and surfaces a typed {@link ApiError};
 * - maps every backend error code (the `{ error: { code, message, correlationId } }`
 *   shape from `backend/app/api/middleware/error_handler.py` + the `AppError` codes in
 *   `backend/app/errors.py`) to a typed {@link ApiError} with a stable error category
 *   (`loai`) and a human-readable message, so both clients recognize the same error
 *   type and show the right failure message (R29.6).
 *
 * Dependency-free by design: it uses the standard `fetch` global (browsers, React
 * Native and Node 18+) and a minimal local typing for the subset of fetch it touches,
 * so the package never depends on the DOM lib, node, axios, react or react-native.
 */

import type { Logger } from "../utils/logger.js";
import type {
  AccountResponse,
  ChangePasswordInput,
  ChunkEditOp,
  ChunkPreview,
  HanMucInput,
  HanMucResponse,
  HistoryItemResponse,
  IndexingResult,
  KetQuaTraLoi,
  KhoaApiInput,
  KhoaApiMasked,
  LimitsInput,
  LoginInput,
  LoginResponse,
  MauPromptInput,
  MauPromptResponse,
  PaginatedDocumentResponse,
  PreviewResult,
  QueryInput,
  RechunkInput,
  RegisterInput,
  ResetPasswordInput,
  ResetRequestInput,
  RetrievalConfigInput,
  RetrievalConfigResponse,
  ShareInput,
  ShareResponse,
  TokenResponse,
  WorkspaceInput,
  WorkspaceResponse,
} from "../types/index.js";

// --- Minimal fetch typing (no DOM lib) -------------------------------------
// We only type the small slice of the fetch API this client uses, so `tsconfig`
// can stay free of the "DOM" lib while remaining strict.

/** Multipart body for file upload. The real `FormData` (browser / React Native) is
 *  structurally assignable to this — typed minimally to avoid the DOM lib. */
export interface FormDataLike {
  append(name: string, value: unknown, fileName?: string): void;
}

/** The subset of the fetch `Response` we read. */
interface FetchResponse {
  ok: boolean;
  status: number;
  statusText: string;
  json(): Promise<unknown>;
  text(): Promise<string>;
}

/** The subset of the request init we send. */
interface FetchRequestInit {
  method: string;
  headers: Record<string, string>;
  body?: string | FormDataLike;
}

/** A `fetch`-compatible function. The ambient global `fetch` satisfies this. */
export type FetchLike = (
  url: string,
  init: FetchRequestInit,
) => Promise<FetchResponse>;

// Ambient declaration of the global `fetch` so this module type-checks without the
// DOM lib. At runtime this resolves to the platform's global fetch (browser / RN /
// Node 18+).
declare const fetch: FetchLike;

// --- Typed errors ----------------------------------------------------------

/**
 * Error category shared by both clients (R29.6). Each value groups one or more
 * backend `AppError` codes so the UI can branch on the KIND of failure (and decide
 * what to do, e.g. re-login on `XAC_THUC`) regardless of platform.
 */
export type LoaiLoi =
  | "XAC_THUC" // authentication: missing/invalid/expired token, disabled account (401)
  | "PHAN_QUYEN" // authorization: insufficient permission (403)
  | "DAU_VAO" // invalid request input (400)
  | "KHONG_TIM_THAY" // target resource not found (404)
  | "XUNG_DOT" // conflict: duplicate email/tenDangNhap... (409)
  | "VUOT_HAN_MUC" // resource quota exceeded
  | "GIOI_HAN_TAN_SUAT" // query rate limit exceeded (429)
  | "BI_KHOA" // account temporarily locked (423)
  | "HE_THONG" // system / unexpected error (500)
  | "KET_NOI"; // network failure: request never reached the server

/** Map a backend `errorCode` to its shared {@link LoaiLoi} category. */
const LOAI_THEO_MA: Record<string, LoaiLoi> = {
  ValidationError: "DAU_VAO",
  AuthenticationError: "XAC_THUC",
  AuthorizationError: "PHAN_QUYEN",
  NotFoundError: "KHONG_TIM_THAY",
  ConflictError: "XUNG_DOT",
  QuotaExceededError: "VUOT_HAN_MUC",
  RateLimitError: "GIOI_HAN_TAN_SUAT",
  LockedError: "BI_KHOA",
  InternalError: "HE_THONG",
  InitializationError: "HE_THONG",
};

/** Default human-readable message per category (used when the server omits one). */
const THONG_DIEP_MAC_DINH: Record<LoaiLoi, string> = {
  XAC_THUC: "Phien dang nhap khong hop le hoac da het han.",
  PHAN_QUYEN: "Ban khong co quyen thuc hien thao tac nay.",
  DAU_VAO: "Du lieu nhap vao khong hop le.",
  KHONG_TIM_THAY: "Khong tim thay tai nguyen yeu cau.",
  XUNG_DOT: "Du lieu bi trung hoac xung dot.",
  VUOT_HAN_MUC: "Da vuot han muc tai nguyen cho phep.",
  GIOI_HAN_TAN_SUAT: "Ban gui yeu cau qua nhanh, vui long thu lai sau.",
  BI_KHOA: "Tai khoan tam thoi bi khoa, vui long thu lai sau.",
  HE_THONG: "Da xay ra loi he thong, vui long thu lai sau.",
  KET_NOI: "Khong the ket noi toi may chu, vui long kiem tra mang.",
};

/** The structured error body returned by the backend global error handler. */
interface BackendErrorBody {
  error?: {
    code?: string;
    message?: string;
    correlationId?: string;
    details?: Record<string, unknown>;
  };
}

/**
 * A typed client-side error. Both clients catch this single type and branch on
 * `loai` to pick the right UI message while keeping the user's input for retry (R29.6).
 */
export class ApiError extends Error {
  /** Shared error category. */
  readonly loai: LoaiLoi;
  /** Raw backend error code (e.g. "ValidationError"); `null` for network failures. */
  readonly errorCode: string | null;
  /** HTTP status code; `0` when the request never reached the server. */
  readonly httpStatus: number;
  /** Backend correlation id for tracing, when present. */
  readonly correlationId: string | null;
  /** Optional structured context returned by the backend (already masked server-side). */
  readonly chiTiet: Record<string, unknown> | null;

  constructor(args: {
    loai: LoaiLoi;
    thongDiep: string;
    errorCode: string | null;
    httpStatus: number;
    correlationId: string | null;
    chiTiet: Record<string, unknown> | null;
  }) {
    super(args.thongDiep);
    this.name = "ApiError";
    this.loai = args.loai;
    this.errorCode = args.errorCode;
    this.httpStatus = args.httpStatus;
    this.correlationId = args.correlationId;
    this.chiTiet = args.chiTiet;
  }
}

/** Build an {@link ApiError} from a non-OK response body + status. */
function mapErrorResponse(httpStatus: number, body: unknown): ApiError {
  const loi = (body as BackendErrorBody | null)?.error;
  const errorCode = loi?.code ?? null;
  const loai: LoaiLoi =
    (errorCode ? LOAI_THEO_MA[errorCode] : undefined) ?? "HE_THONG";
  const thongDiep =
    loi?.message && loi.message.trim().length > 0
      ? loi.message
      : THONG_DIEP_MAC_DINH[loai];
  return new ApiError({
    loai,
    thongDiep,
    errorCode,
    httpStatus,
    correlationId: loi?.correlationId ?? null,
    chiTiet: loi?.details ?? null,
  });
}

// --- Factory ---------------------------------------------------------------

/** Options for {@link createApiClient}. */
export interface ApiClientOptions {
  /** API origin, e.g. "https://api.example.com" or "" for same-origin/relative. */
  baseURL: string;
  /** Returns the current session token (or null when logged out). STORAGE is the
   *  caller's concern — this package never reads/writes any token store. */
  getToken: () => string | null | undefined;
  /** Invoked on HTTP 401 so the caller can clear its token / redirect. */
  onUnauthorized: () => void;
  /** Optional logger (injected sink). When omitted, the client logs nothing. */
  logger?: Logger;
  /** Optional fetch implementation override (defaults to the global `fetch`). */
  fetchImpl?: FetchLike;
}

/** Per-request options used internally by {@link makeRequest}. */
interface RequestOptions {
  /** JSON request body (serialized with JSON.stringify, sets Content-Type). */
  body?: unknown;
  /** Multipart body (file upload); Content-Type is left to fetch (boundary). */
  formData?: FormDataLike;
  /** Query string parameters (undefined/null values are skipped). */
  query?: Record<string, string | number | undefined | null>;
}

/** Join the baseURL + path + optional query string into a request URL. */
function buildUrl(
  baseURL: string,
  path: string,
  query?: RequestOptions["query"],
): string {
  const goc = baseURL.replace(/\/+$/, "");
  let url = `${goc}${path}`;
  if (query) {
    const phan: string[] = [];
    for (const [khoa, giaTri] of Object.entries(query)) {
      if (giaTri === undefined || giaTri === null) continue;
      phan.push(
        `${encodeURIComponent(khoa)}=${encodeURIComponent(String(giaTri))}`,
      );
    }
    if (phan.length > 0) url += `?${phan.join("&")}`;
  }
  return url;
}

/**
 * The shared API client. Every method maps 1:1 to a backend endpoint; method names
 * are English verbs, fields/DTOs are the Vietnamese-no-diacritics shared types.
 */
export interface ApiClient {
  // Authentication / account (R1, R2, R25)
  register(input: RegisterInput): Promise<RegisterResponse>;
  login(input: LoginInput): Promise<LoginResponse>;
  logout(): Promise<void>;
  refresh(): Promise<TokenResponse>;
  changePassword(input: ChangePasswordInput): Promise<void>;
  requestPasswordReset(input: ResetRequestInput): Promise<void>;
  resetPassword(input: ResetPasswordInput): Promise<void>;
  deleteAccount(): Promise<void>;

  // Workspaces + sharing + retrieval config (R3, R4, R11, R19)
  listWorkspaces(): Promise<WorkspaceResponse[]>;
  createWorkspace(input: WorkspaceInput): Promise<WorkspaceResponse>;
  updateWorkspace(id: string, input: WorkspaceInput): Promise<WorkspaceResponse>;
  deleteWorkspace(id: string): Promise<void>;
  grantShare(id: string, input: ShareInput): Promise<ShareResponse>;
  revokeShare(id: string, taiKhoanId: string): Promise<void>;
  getRetrievalConfig(id: string): Promise<RetrievalConfigResponse>;
  updateRetrievalConfig(
    id: string,
    input: RetrievalConfigInput,
  ): Promise<RetrievalConfigResponse>;

  // Documents / chunk preview (R5, R18)
  uploadDocument(
    khongGianId: string,
    formData: FormDataLike,
  ): Promise<PreviewResult>;
  listDocuments(
    khongGianId: string,
    page?: number,
    pageSize?: number,
  ): Promise<PaginatedDocumentResponse>;
  getChunks(taiLieuId: string): Promise<PreviewResult>;
  editChunks(taiLieuId: string, ops: ChunkEditOp[]): Promise<ChunkPreview[]>;
  commitDocument(taiLieuId: string): Promise<IndexingResult>;
  rechunkDocument(
    taiLieuId: string,
    input?: RechunkInput,
  ): Promise<PreviewResult>;
  resetDocument(taiLieuId: string): Promise<PreviewResult>;
  deleteDocument(taiLieuId: string): Promise<void>;

  // Query (R6, R16)
  queryWorkspace(
    khongGianId: string,
    input: QueryInput,
  ): Promise<KetQuaTraLoi>;

  // History (R9)
  listHistory(khongGianId: string): Promise<HistoryItemResponse[]>;
  deleteHistory(lichSuId: string): Promise<void>;

  // Admin (R10, R12, R20, R23)
  listUsers(): Promise<AccountResponse[]>;
  disableUser(id: string): Promise<void>;
  enableUser(id: string): Promise<void>;
  setUserQuota(id: string, input: HanMucInput): Promise<HanMucResponse>;
  getPrompt(vaiTro: string): Promise<MauPromptResponse>;
  updatePrompt(vaiTro: string, input: MauPromptInput): Promise<MauPromptResponse>;
  updateLimits(input: LimitsInput): Promise<LimitsInput>;

  // Account API keys / BYOK (R22)
  listApiKeys(): Promise<KhoaApiMasked[]>;
  setApiKey(input: KhoaApiInput): Promise<void>;
  deleteApiKey(providerTen: string, vaiTro: string): Promise<void>;
}

/** Result of {@link ApiClient.register} (R1) — id + email + tenDangNhap, no hash. */
export interface RegisterResponse {
  id: string;
  email: string;
  tenDangNhap: string;
}

/**
 * Create the shared API client. `getToken` keeps token storage out of this package,
 * `onUnauthorized` lets the caller react to a 401, and every endpoint is exposed as a
 * typed method. The same instance behaves identically on Web and Mobile (R29).
 */
export function createApiClient(options: ApiClientOptions): ApiClient {
  const { baseURL, getToken, onUnauthorized, logger } = options;
  const doFetch: FetchLike = options.fetchImpl ?? fetch;

  /**
   * Perform one HTTP request: attach Bearer token, send JSON or multipart, then map
   * the response to `T` (204 → undefined) or throw a typed {@link ApiError}.
   */
  async function makeRequest<T>(
    method: string,
    path: string,
    opts: RequestOptions = {},
  ): Promise<T> {
    const url = buildUrl(baseURL, path, opts.query);
    const headers: Record<string, string> = {};

    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const init: FetchRequestInit = { method, headers };
    if (opts.formData !== undefined) {
      // multipart/form-data: let fetch set Content-Type (with the boundary).
      init.body = opts.formData;
    } else if (opts.body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(opts.body);
    }

    logger?.debug("Gui yeu cau API", { method, path });

    let res: FetchResponse;
    try {
      res = await doFetch(url, init);
    } catch (loiMang) {
      // Request never reached the server (offline / DNS / CORS at the transport level).
      logger?.error("Loi ket noi API", {
        method,
        path,
        loi: String(loiMang),
      });
      throw new ApiError({
        loai: "KET_NOI",
        thongDiep: THONG_DIEP_MAC_DINH.KET_NOI,
        errorCode: null,
        httpStatus: 0,
        correlationId: null,
        chiTiet: null,
      });
    }

    if (!res.ok) {
      const body = await parseBodySafely(res);
      if (res.status === 401) {
        // Let the caller clear its token / redirect, then surface the typed error.
        onUnauthorized();
      }
      const apiError = mapErrorResponse(res.status, body);
      logger?.warn("Yeu cau API that bai", {
        method,
        path,
        httpStatus: apiError.httpStatus,
        loai: apiError.loai,
        errorCode: apiError.errorCode,
        correlationId: apiError.correlationId,
      });
      throw apiError;
    }

    // 204 No Content (logout/delete/change-password...) → no body to parse.
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  /** Parse a (possibly empty / non-JSON) error body without throwing. */
  async function parseBodySafely(res: FetchResponse): Promise<unknown> {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }

  const enc = encodeURIComponent;

  return {
    // --- Authentication / account ----------------------------------------
    register: (input) =>
      makeRequest<RegisterResponse>("POST", "/api/auth/register", {
        body: input,
      }),
    login: (input) =>
      makeRequest<LoginResponse>("POST", "/api/auth/login", { body: input }),
    logout: () => makeRequest<void>("POST", "/api/auth/logout"),
    refresh: () => makeRequest<TokenResponse>("POST", "/api/auth/refresh"),
    changePassword: (input) =>
      makeRequest<void>("POST", "/api/auth/password/change", { body: input }),
    requestPasswordReset: (input) =>
      makeRequest<void>("POST", "/api/auth/password/reset-request", {
        body: input,
      }),
    resetPassword: (input) =>
      makeRequest<void>("POST", "/api/auth/password/reset", { body: input }),
    deleteAccount: () => makeRequest<void>("DELETE", "/api/account"),

    // --- Workspaces + sharing + retrieval config -------------------------
    listWorkspaces: () =>
      makeRequest<WorkspaceResponse[]>("GET", "/api/workspaces"),
    createWorkspace: (input) =>
      makeRequest<WorkspaceResponse>("POST", "/api/workspaces", { body: input }),
    updateWorkspace: (id, input) =>
      makeRequest<WorkspaceResponse>("PATCH", `/api/workspaces/${enc(id)}`, {
        body: input,
      }),
    deleteWorkspace: (id) =>
      makeRequest<void>("DELETE", `/api/workspaces/${enc(id)}`),
    grantShare: (id, input) =>
      makeRequest<ShareResponse>("POST", `/api/workspaces/${enc(id)}/shares`, {
        body: input,
      }),
    revokeShare: (id, taiKhoanId) =>
      makeRequest<void>(
        "DELETE",
        `/api/workspaces/${enc(id)}/shares/${enc(taiKhoanId)}`,
      ),
    getRetrievalConfig: (id) =>
      makeRequest<RetrievalConfigResponse>(
        "GET",
        `/api/workspaces/${enc(id)}/retrieval-config`,
      ),
    updateRetrievalConfig: (id, input) =>
      makeRequest<RetrievalConfigResponse>(
        "PUT",
        `/api/workspaces/${enc(id)}/retrieval-config`,
        { body: input },
      ),

    // --- Documents / chunk preview ---------------------------------------
    uploadDocument: (khongGianId, formData) =>
      makeRequest<PreviewResult>(
        "POST",
        `/api/workspaces/${enc(khongGianId)}/documents`,
        { formData },
      ),
    listDocuments: (khongGianId, page, pageSize) =>
      makeRequest<PaginatedDocumentResponse>(
        "GET",
        `/api/workspaces/${enc(khongGianId)}/documents`,
        { query: { page, pageSize } },
      ),
    getChunks: (taiLieuId) =>
      makeRequest<PreviewResult>(
        "GET",
        `/api/documents/${enc(taiLieuId)}/chunks`,
      ),
    editChunks: (taiLieuId, ops) =>
      makeRequest<ChunkPreview[]>(
        "PUT",
        `/api/documents/${enc(taiLieuId)}/chunks`,
        { body: ops },
      ),
    commitDocument: (taiLieuId) =>
      makeRequest<IndexingResult>(
        "POST",
        `/api/documents/${enc(taiLieuId)}/commit`,
      ),
    rechunkDocument: (taiLieuId, input) =>
      makeRequest<PreviewResult>(
        "POST",
        `/api/documents/${enc(taiLieuId)}/rechunk`,
        input ? { body: input } : {},
      ),
    resetDocument: (taiLieuId) =>
      makeRequest<PreviewResult>(
        "POST",
        `/api/documents/${enc(taiLieuId)}/reset`,
      ),
    deleteDocument: (taiLieuId) =>
      makeRequest<void>("DELETE", `/api/documents/${enc(taiLieuId)}`),

    // --- Query -----------------------------------------------------------
    queryWorkspace: (khongGianId, input) =>
      makeRequest<KetQuaTraLoi>(
        "POST",
        `/api/workspaces/${enc(khongGianId)}/query`,
        { body: input },
      ),

    // --- History ---------------------------------------------------------
    listHistory: (khongGianId) =>
      makeRequest<HistoryItemResponse[]>(
        "GET",
        `/api/workspaces/${enc(khongGianId)}/history`,
      ),
    deleteHistory: (lichSuId) =>
      makeRequest<void>("DELETE", `/api/history/${enc(lichSuId)}`),

    // --- Admin -----------------------------------------------------------
    listUsers: () => makeRequest<AccountResponse[]>("GET", "/api/admin/users"),
    disableUser: (id) =>
      makeRequest<void>("POST", `/api/admin/users/${enc(id)}/disable`),
    enableUser: (id) =>
      makeRequest<void>("POST", `/api/admin/users/${enc(id)}/enable`),
    setUserQuota: (id, input) =>
      makeRequest<HanMucResponse>("PUT", `/api/admin/users/${enc(id)}/quota`, {
        body: input,
      }),
    getPrompt: (vaiTro) =>
      makeRequest<MauPromptResponse>("GET", `/api/admin/prompts/${enc(vaiTro)}`),
    updatePrompt: (vaiTro, input) =>
      makeRequest<MauPromptResponse>(
        "PUT",
        `/api/admin/prompts/${enc(vaiTro)}`,
        { body: input },
      ),
    updateLimits: (input) =>
      makeRequest<LimitsInput>("PUT", "/api/admin/limits", { body: input }),

    // --- Account API keys / BYOK -----------------------------------------
    listApiKeys: () =>
      makeRequest<KhoaApiMasked[]>("GET", "/api/account/api-keys"),
    setApiKey: (input) =>
      makeRequest<void>("PUT", "/api/account/api-keys", { body: input }),
    deleteApiKey: (providerTen, vaiTro) =>
      makeRequest<void>("DELETE", "/api/account/api-keys", {
        query: { providerTen, vaiTro },
      }),
  };
}
