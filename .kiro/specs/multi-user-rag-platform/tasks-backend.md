# Implementation Plan — Backend (BE)

> Nhóm 1/4 của Multi-User RAG Platform. Xem `tasks.md` cho tổng quan, Notes và Task Dependency Graph chung.

## Quy ước (theo steering)
- Đặt tên: entity/field tiếng Việt không dấu (`taiKhoan`, `khongGianId`); verb/method tiếng Anh (`createWorkspace`, `verifyToken`); UI label tiếng Việt có dấu.
- Logging tập trung từ task đầu tiên; mọi module ghi log qua logger chung, che trường nhạy cảm, không nuốt lỗi im lặng.
- Property-based test (PBT): backend dùng **Hypothesis**; mỗi correctness property hiện thực bằng **đúng một** PBT chạy tối thiểu 100 vòng, gắn comment `# Feature: multi-user-rag-platform, Property {n}: ...`.
- LLM/Embedding provider được **mock** trong PBT.

---

## Tasks

- [x] 1. Thiết lập hạ tầng backend và logging tập trung
  - [x] 1.1 Scaffold package `app` + cấu hình dự án
    - Tạo cấu trúc `backend/app/` (`main.py`, `config.py`, `db/`, `auth/`, `api/`, `services/`, `pipelines/`, `chunking/`, `providers/`, `prompts/`, `models/`)
    - Khai báo dependencies (FastAPI, uvicorn, SQLAlchemy, pydantic-settings, bcrypt, cryptography/Fernet, hypothesis, pytest) trong `requirements.txt`/`pyproject.toml`
    - `config.py`: `Settings` (pydantic-settings) với mọi mặc định và khoảng hợp lệ (session_ttl=60, llm_timeout=30, max_file_size=50MB, ngưỡng 0.3/0.5, k=8, quotas 50/5GB/1000, `secret_key_encrypt`), neo `env_file` tuyệt đối vào `backend/.env`
    - `main.py`: tạo FastAPI app + lifespan/DI skeleton + CORS
    - _Requirements: 23.3, 13.1_

  - [x] 1.2 Cấu hình logging tập trung + correlationId + che dữ liệu nhạy cảm
    - `logging_config.py`: một `setup_logging()` duy nhất (console + file, level theo môi trường: prod=INFO, dev=DEBUG), định dạng kèm timestamp/level/nguồn/message
    - Middleware `correlation.py`: sinh/đính `correlationId` cho mỗi request, log INFO method+path+correlationId
    - Util che trường nhạy cảm (mật khẩu, token, khóa API, PII) dùng chung khi format log
    - _Requirements: 14.1, 14.2, 14.4, 14.5, 14.6, 14.7_

  - [x] 1.3 Thiết lập SQLAlchemy
    - `db/database.py`: engine/session factory, `init_db()`; hỗ trợ đổi sang Postgres qua connection string
    - _Requirements: 14.1_

  - [x] 1.4 Global error handler + phân loại lỗi miền
    - `api/middleware/error_handler.py`: ánh xạ `ValidationError`/`AuthenticationError`/`AuthorizationError`/`NotFoundError`/`ConflictError`/`QuotaExceededError`/`RateLimitError`/`LockedError`/`InternalError` → HTTP code; gắn `correlationId`, log ERROR kèm stack, KHÔNG nuốt lỗi
    - _Requirements: 14.3_

  - [x] 1.5 Hạ tầng registry tự-đăng-ký + fail-fast khi khởi tạo
    - `chunking/registry.py` + `providers/registry.py`: decorator đăng ký + auto-discover `*_chunker.py`/`*_provider.py`, không sửa factory khi thêm mới
    - Khi khởi tạo: phát hiện provider/chiến lược không tồn tại hoặc thiếu vai trò bắt buộc → dừng khởi tạo và phát `InitializationError` nêu rõ tên
    - _Requirements: 13.2, 13.3, 13.5, 17.2, 21.2, 21.3_

  - [x]* 1.6 Property test: log che mọi trường nhạy cảm
    - **Property 56: Log loại trừ/che mọi trường nhạy cảm**
    - **Validates: Requirements 14.4, 22.3**

  - [x]* 1.7 Property test: log entry đủ trường + correlationId
    - **Property 57: Mỗi log entry đủ trường bắt buộc và có định danh truy vết**
    - **Validates: Requirements 14.5, 14.6**

  - [x]* 1.8 Unit test: fail-fast khi provider/embedding không tồn tại hoặc thiếu vai trò
    - Kiểm tra dừng khởi tạo + thông điệp nêu tên không hợp lệ; chuẩn hóa trống dùng provider xác minh là hợp lệ
    - _Requirements: 13.3, 13.5, 21.3, 17.7_

- [x] 2. Định nghĩa mô hình dữ liệu (ORM + DTO)
  - [x] 2.1 ORM models + enums
    - `db/models.py`: `TaiKhoan`, `PhienXacThuc`, `KhongGianTaiLieu`, `ChiaSe`, `TaiLieu`, `Chunk`, `TomTatTaiLieu`, `QuyTacRanhGioi`, `CauHinhTruyXuat`, `MauPrompt`, `KhoaApiNguoiDung`, `HanMuc`, `LichSuTroChuyen`, `TrichDan`; enums `VaiTro`/`TrangThaiTaiKhoan`/`MucQuyen`/`TrangThaiTaiLieu`/`NhanXacMinh`; ràng buộc UNIQUE(email), UNIQUE(tenDangNhap), UNIQUE(khongGianId, taiKhoanId)
    - _Requirements: 1.1, 2.1, 3.8, 4.1, 5.13, 9.1, 11.1, 12.1, 22.2_

  - [x] 2.2 Pydantic DTO schemas
    - `models/schemas.py`: `RegisterInput`, `LoginInput`, `ChangePasswordInput`, `ResetPasswordInput`, `WorkspaceInput`, `ShareInput`, `RetrievalConfigInput`, `DocumentMetadataInput`, `PreviewResult`, `IndexingResult`, `ChunkEditOp`, `QueryInput`, `KetQuaTraLoi`, `KhoaApiInput`, `KhoaApiMasked`, `HanMucInput`, `LimitsInput`
    - _Requirements: 6.1, 7.5, 8.1, 22.3_

- [x] 3. Triển khai Auth_Service
  - [x] 3.1 Băm mật khẩu + validate độ dài
    - `auth/password.py`: `hashPassword`/`verifyPassword` (bcrypt), validate matKhau 8–64, không lưu plaintext
    - _Requirements: 1.2_

  - [x]* 3.2 Property test: băm mật khẩu
    - **Property 1: Băm mật khẩu không lưu plaintext và xác minh đúng**
    - **Validates: Requirements 1.2**

  - [x] 3.3 Token HMAC + PhienXacThuc (create/verify/revoke)
    - `auth/tokens.py`: tạo token HMAC có hạn gắn `jti` → bản ghi `PhienXacThuc`; `verifyToken` kiểm tra hết hạn + thu hồi + tài khoản HOAT_DONG
    - _Requirements: 2.5, 2.6, 2.7, 2.9, 10.8_

  - [x]* 3.4 Property test: tính hợp lệ của token
    - **Property 7: Token hợp lệ khi và chỉ khi còn hạn, chưa thu hồi, tài khoản hoạt động**
    - **Validates: Requirements 2.5, 2.7, 2.8, 2.9, 10.8**

  - [x] 3.5 `register()`
    - `auth/auth_service.py`: validate email (≤254, định dạng), tenDangNhap (3–30), matKhau (8–64), trường bắt buộc; trùng email/tenDangNhap → ConflictError nêu rõ trường; VaiTro mặc định NGUOI_DUNG
    - _Requirements: 1.1, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x]* 3.6 Property test: đăng ký hợp lệ
    - **Property 2: Đăng ký hợp lệ tạo tài khoản NGUOI_DUNG**
    - **Validates: Requirements 1.1**

  - [x]* 3.7 Property test: đăng ký không hợp lệ luôn bị từ chối
    - **Property 3: Đăng ký không hợp lệ luôn bị từ chối, không tạo tài khoản**
    - **Validates: Requirements 1.3, 1.4, 1.5, 1.6, 1.7, 2.3**

  - [x] 3.8 `login()` + lockout + tài khoản vô hiệu hóa
    - Kiểm tra lockout (5 fail/15 phút), tài khoản VO_HIEU_HOA → từ chối; thành công tạo PhienXacThuc (hạn = session_ttl) + trả token kèm VaiTro; lỗi xác thực chung không lộ trường
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 10.7_

  - [x]* 3.9 Property test: đăng nhập đúng tạo phiên kèm vai trò
    - **Property 4: Đăng nhập đúng tạo phiên hợp lệ kèm vai trò**
    - **Validates: Requirements 2.1**

  - [x]* 3.10 Property test: thông báo lỗi đăng nhập chung chung
    - **Property 5: Thông báo lỗi đăng nhập sai là chung chung và bất biến**
    - **Validates: Requirements 2.2**

  - [x]* 3.11 Property test: khóa đăng nhập sau 5 lần thất bại
    - **Property 6: Khóa đăng nhập sau 5 lần thất bại liên tiếp**
    - **Validates: Requirements 2.4**

  - [x]* 3.12 Property test: tài khoản vô hiệu hóa
    - **Property 8: Tài khoản bị vô hiệu hóa không đăng nhập được và bị thu hồi phiên**
    - **Validates: Requirements 10.7, 10.8**

  - [x] 3.13 logout / refresh / changePassword / reset password / tự xóa tài khoản
    - `logout` (revoke phiên), `refreshSession`, `changePassword` (revoke phiên khác), `requestPasswordReset` (phản hồi chung chung), `resetPassword` (single-use + hết hạn), `deleteOwnAccount` (xóa/ẩn danh dữ liệu + thu hồi mọi phiên); `auth/crypto.py` dùng chung cho khóa reset nếu cần
    - _Requirements: 2.8, 25.1, 25.2, 25.3, 25.4, 25.5, 25.6_

  - [x]* 3.14 Property test: đổi mật khẩu thu hồi phiên khác
    - **Property 9: Đổi mật khẩu thu hồi các phiên khác**
    - **Validates: Requirements 25.1**

  - [x]* 3.15 Property test: phản hồi reset không tiết lộ email
    - **Property 10: Phản hồi đặt lại mật khẩu không tiết lộ tồn tại email**
    - **Validates: Requirements 25.3**

  - [x]* 3.16 Property test: liên kết reset dùng một lần + hết hạn
    - **Property 11: Liên kết đặt lại dùng một lần và hết hạn**
    - **Validates: Requirements 25.4**

  - [x]* 3.17 Property test: làm mới phiên cấp token mới
    - **Property 12: Làm mới phiên cấp token mới hợp lệ**
    - **Validates: Requirements 25.5**

  - [x]* 3.18 Property test: tự xóa tài khoản
    - **Property 58: Tự xóa tài khoản loại bỏ toàn bộ dữ liệu và thu hồi phiên**
    - **Validates: Requirements 25.6**

- [x] 4. Ủy quyền và dependency injection
  - [x] 4.1 `resolveAccess` + DI dependencies
    - `services/share_service.py::resolveAccess` (NONE/CHI_DOC/GHI/CHU_SO_HUU); `api/dependencies.py`: `get_db`, `get_current_user`, `require_role`, `require_workspace_access`; thiếu token → 401, thiếu quyền → 403/404
    - _Requirements: 2.6, 3.2, 3.3, 3.6, 10.6_

  - [x]* 4.2 Property test: thực thi ủy quyền nhất quán
    - **Property 14: Ủy quyền truy cập được thực thi nhất quán**
    - **Validates: Requirements 3.2, 3.3, 4.5, 5.2, 6.2, 9.4, 11.7, 18.5, 19.5**

  - [x]* 4.3 Property test: thao tác quản trị chỉ cho QUAN_TRI
    - **Property 15: Thao tác quản trị chỉ dành cho QUAN_TRI**
    - **Validates: Requirements 10.6, 20.4**

- [x] 5. WorkspaceService và ShareService
  - [x] 5.1 WorkspaceService CRUD (xóa có giao dịch)
    - `createWorkspace` (trim ten 1–100, kiểm HanMuc atomic), `renameWorkspace`, `updateDescription` (≤1000), `deleteWorkspace` (transaction: xóa Chunk/collection + TaiLieu + TrichDan, đánh dấu LichSu; rollback khi lỗi); `listWorkspaces` chỉ sở hữu + được chia sẻ
    - _Requirements: 3.1, 4.1, 4.2, 4.3, 4.4, 4.6, 4.7, 4.8, 12.1_

  - [x]* 5.2 Property test: hợp lệ hóa tên/mô tả không gian
    - **Property 18: Hợp lệ hóa tên và mô tả không gian**
    - **Validates: Requirements 4.1, 4.2, 4.3, 4.4**

  - [x]* 5.3 Property test: xóa không gian toàn vẹn + hoàn tác
    - **Property 19: Xóa không gian là toàn vẹn và có hoàn tác**
    - **Validates: Requirements 4.6, 4.7, 4.8**

  - [x]* 5.4 Property test: liệt kê không gian cô lập
    - **Property 13: Liệt kê không gian chỉ trả về sở hữu và được chia sẻ**
    - **Validates: Requirements 3.1**

  - [x] 5.5 ShareService grant/revoke
    - `grantShare` (mucQuyen ∈ {CHI_DOC, GHI}; ngoài tập → 400; đích không tồn tại → 404), `revokeShare` (sau đó truy cập → 403); chỉ chủ sở hữu
    - _Requirements: 11.1, 11.4, 11.5, 11.6, 11.7_

  - [x]* 5.6 Property test: ma trận quyền chia sẻ
    - **Property 43: Ma trận quyền chia sẻ**
    - **Validates: Requirements 11.2, 11.3**

  - [x]* 5.7 Property test: cấp/thu hồi quyền chia sẻ round-trip
    - **Property 44: Cấp và thu hồi quyền chia sẻ là round-trip**
    - **Validates: Requirements 11.1, 11.6**

- [x] 6. QuotaService và giới hạn tần suất
  - [x] 6.1 QuotaService (kiểm tra + đặt chỗ nguyên tử)
    - `checkAndReserve` (khóa giao dịch, kiểm tại biên), `releaseQuota`, `setQuota` (range hợp lệ)
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_

  - [x]* 6.2 Property test: áp hạn mức nguyên tử
    - **Property 45: Áp hạn mức nguyên tử tại biên và khi tương tranh**
    - **Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.7**

  - [x]* 6.3 Property test: hợp lệ hóa cấu hình hạn mức
    - **Property 46: Hợp lệ hóa cấu hình hạn mức**
    - **Validates: Requirements 12.5, 12.6**

  - [x] 6.4 RateLimiter theo TaiKhoan
    - `api/middleware/rate_limit.py`: vượt hạn mức tần suất → 429, không gọi LLM
    - _Requirements: 24.1, 24.2_

  - [x]* 6.5 Property test: vượt giới hạn tần suất
    - **Property 47: Vượt giới hạn tần suất bị từ chối và không gọi LLM**
    - **Validates: Requirements 24.1, 24.2**

- [x] 7. Chiến lược chunk qua registry
  - [x] 7.1 ChunkerBase + các chiến lược
    - `chunking/`: recursive, structure-aware (markdown), page, semantic, vietnamese-law (kế thừa, đăng ký tên "vietnamese-law"); mọi chiến lược áp dụng cho TaiLieu đa lĩnh vực, tạo ≥1 Chunk với văn bản không rỗng
    - _Requirements: 15.1, 15.2, 17.1_

  - [x] 7.2 AutoSelector theo thứ tự ưu tiên cố định
    - `chunking/auto_selector.py`: (1) vietnamese-law nếu "Điều"+chữ số đầu dòng, (2) structure-aware nếu heading markdown, (3) page nếu PDF phân trang, (4) recursive; chiến lược không tồn tại → từ chối nêu tên
    - _Requirements: 17.3, 17.4, 17.5, 17.6, 17.7, 17.8_

  - [x]* 7.3 Property test: chọn chiến lược tự động
    - **Property 27: Chọn chiến lược "Tự động" theo thứ tự ưu tiên cố định**
    - **Validates: Requirements 17.3, 17.4, 17.5, 17.6, 17.8**

  - [x]* 7.4 Property test: mọi văn bản không rỗng tạo ít nhất một Chunk
    - **Property 22: Mọi văn bản không rỗng tạo ít nhất một Chunk**
    - **Validates: Requirements 15.1, 15.2**

  - [x]* 7.5 Unit test: chọn chiến lược theo cấu hình + từ chối chiến lược không tồn tại
    - _Requirements: 17.1, 17.7_

- [x] 8. Document_Pipeline
  - [x] 8.1 Upload → parse → chunk → preview (máy trạng thái, chưa embed)
    - `pipelines/document_pipeline.py::uploadDocument`: validate quyền GHI, định dạng, kích thước (cấu hình), quota dung lượng + số tài liệu (atomic); parse → chunk; 0 chunk → từ chối; trạng thái DA_PARSE_CHO_DUYET; trả soChunk + danh sách Chunk preview; KHÔNG embed
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.11, 5.13, 5.14, 12.2, 12.3_

  - [x]* 8.2 Property test: vector tồn tại khi và chỉ khi DA_EMBED
    - **Property 20: Vector tồn tại khi và chỉ khi tài liệu ở trạng thái DA_EMBED**
    - **Validates: Requirements 5.1, 5.13, 5.14**

  - [x]* 8.3 Property test: bản xem trước phản ánh đầy đủ Chunk
    - **Property 21: Bản xem trước phản ánh đầy đủ Chunk theo thứ tự**
    - **Validates: Requirements 18.1**

  - [x] 8.4 commitEmbedding + Embedding_Provider theo không gian
    - `commitEmbedding`: chốt → embed (Embedding_Provider của không gian) → lưu collection → DA_EMBED; tích hợp registry embedding; ghi collection tạm rồi swap nguyên tử
    - _Requirements: 5.13, 21.1, 21.4_

  - [x] 8.5 rechunk / editChunks / setBoundaryRules / resetToDefault (idempotent)
    - Cắt lại idempotent (xóa sạch Chunk cũ trước khi ghi mới); sửa tay (merge/split/adjust, từ chối Chunk rỗng); khai báo QuyTacRanhGioi (lưu dữ liệu) + áp khi cắt lại; reset về mặc định; lỗi giữ Chunk cũ; NGUOI_DUNG có quyền ghi tự thực hiện không cần sửa mã
    - _Requirements: 5.6, 5.12, 18.2, 18.3, 18.4, 18.5, 18.6, 18.7, 18.8, 18.9, 18.10, 18.11, 21.4_

  - [x]* 8.6 Property test: cắt lại/embed lại idempotent
    - **Property 23: Cắt lại / embed lại là idempotent và thay sạch**
    - **Validates: Requirements 5.12, 18.2, 18.6, 18.7, 21.4**

  - [x]* 8.7 Property test: cắt lại thất bại giữ nguyên Chunk cũ
    - **Property 24: Cắt lại / embed lại thất bại giữ nguyên Chunk cũ**
    - **Validates: Requirements 5.6, 18.10**

  - [x]* 8.8 Property test: sửa tay Chunk bảo toàn nội dung
    - **Property 25: Sửa tay Chunk bảo toàn nội dung và từ chối Chunk rỗng**
    - **Validates: Requirements 18.3, 18.11**

  - [x]* 8.9 Property test: áp dụng QuyTacRanhGioi khi cắt lại
    - **Property 26: QuyTacRanhGioi được áp dụng khi cắt lại**
    - **Validates: Requirements 18.4**

  - [x] 8.10 buildSummary + listDocuments (phân trang) + deleteDocument
    - `buildSummary` (TomTatTaiLieu + outline khi nạp); `listDocuments` (page≥1, pageSize 1–100, mặc định 20, kèm tổng số); `deleteDocument` (xóa Chunk khỏi Vector_Store, không tồn tại → 404)
    - _Requirements: 5.7, 5.8, 5.9, 5.10_

  - [x]* 8.11 Property test: xóa tài liệu + phân trang
    - **Property 28: Xóa tài liệu loại bỏ Chunk; liệt kê phân trang đúng**
    - **Validates: Requirements 5.7, 5.8**

  - [x]* 8.12 Unit test: NGUOI_DUNG tự phục vụ chỉnh chunk không cần sửa mã
    - _Requirements: 18.5, 18.8, 18.9_

- [x] 9. ApiKeyService (BYOK) và mã hóa khóa
  - [x] 9.1 Fernet crypto + ApiKeyService
    - `auth/crypto.py` (encrypt/decrypt Fernet); `setApiKey`/`getApiKey`/`getMaskedKeys`/`deleteApiKey`; phân giải khóa: khóa người dùng → khóa hệ thống; thiếu khóa bắt buộc → lỗi rõ ràng, không lộ chi tiết, không gọi provider; cô lập khóa giữa người dùng
    - _Requirements: 22.1, 22.2, 22.3, 22.4, 22.5, 22.6, 22.7_

  - [x]* 9.2 Property test: mã hóa khóa round-trip
    - **Property 52: Mã hóa khóa là round-trip và không lưu plaintext**
    - **Validates: Requirements 22.2**

  - [x]* 9.3 Property test: khóa luôn được che khi xuất
    - **Property 53: Khóa API luôn được che khi xuất ra ngoài**
    - **Validates: Requirements 22.3**

  - [x]* 9.4 Property test: dùng đúng khóa người dùng + cô lập
    - **Property 54: Dùng đúng khóa của người dùng và cô lập giữa người dùng**
    - **Validates: Requirements 22.4, 22.5**

  - [x]* 9.5 Property test: dự phòng khóa hệ thống hoặc báo lỗi
    - **Property 55: Dự phòng khóa hệ thống hoặc báo lỗi không gọi provider**
    - **Validates: Requirements 22.7**

- [x] 10. Query_Pipeline
  - [x] 10.1 Validate câu hỏi + normalizeQuestion + classifyIntent + ép chế độ
    - `pipelines/query_pipeline.py`: validate cauHoi 1–1000; chuẩn hóa không dấu (guard cùng bộ từ sau bỏ dấu, lệch → giữ gốc); `classifyIntent` xác định theo mẫu/từ khóa cấu hình; ép chế độ ghi đè phân loại
    - _Requirements: 6.3, 6.7, 16.6, 16.7, 16.8_

  - [x]* 10.2 Property test: hợp lệ hóa độ dài câu hỏi
    - **Property 29: Hợp lệ hóa độ dài câu hỏi**
    - **Validates: Requirements 6.3**

  - [x]* 10.3 Property test: chuẩn hóa giữ nguyên bộ từ
    - **Property 32: Chuẩn hóa câu hỏi không dấu giữ nguyên bộ từ**
    - **Validates: Requirements 6.7**

  - [x]* 10.4 Property test: phân loại ý định xác định
    - **Property 39: Phân loại ý định là xác định**
    - **Validates: Requirements 16.6**

  - [x]* 10.5 Property test: ép chế độ ghi đè phân loại
    - **Property 40: Ép chế độ trả lời ghi đè phân loại tự động**
    - **Validates: Requirements 16.7, 16.8**

  - [x] 10.6 retrieve hybrid RRF + gating ngưỡng (giới hạn collection)
    - Embed + hybrid search (vector + BM25 hợp nhất RRF) chỉ trên collection của không gian, k + trọng số từ CauHinhTruyXuat; gating: < ngưỡng "không tìm thấy" → "không tìm thấy", trong khoảng giữa → "chưa đủ liên quan", cả hai KHÔNG gọi LLM tổng hợp
    - _Requirements: 3.4, 6.1, 6.4, 6.5, 6.6_

  - [x]* 10.7 Property test: RRF tối đa k + đúng thứ tự
    - **Property 30: Hợp nhất RRF trả tối đa k và đúng thứ tự**
    - **Validates: Requirements 6.4**

  - [x]* 10.8 Property test: lọc ngưỡng không gọi LLM
    - **Property 31: Lọc ngưỡng không gọi LLM tổng hợp**
    - **Validates: Requirements 6.5, 6.6**

  - [x]* 10.9 Property test: truy xuất cô lập theo không gian
    - **Property 16: Truy xuất chỉ trả Chunk thuộc không gian được truy vấn**
    - **Validates: Requirements 3.4, 6.1**

  - [x] 10.10 synthesize ([n] + TrichDan) + verifyAnswer (async) + fallback
    - Tổng hợp strict grounding chèn marker `[n]` ↔ TrichDan; xác minh chéo bất đồng bộ gắn nhãn { đã xác minh / có mâu thuẫn / chưa xác minh }; lỗi xác minh → "chưa xác minh"; lỗi/timeout tổng hợp → trả chunk gốc kèm cờ `laFallback`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6, 8.1, 8.2, 8.3, 8.4_

  - [x]* 10.11 Property test: marker trích dẫn song ánh
    - **Property 33: Marker trích dẫn nằm trong 1..N và song ánh với danh sách TrichDan**
    - **Validates: Requirements 7.4, 7.5**

  - [x]* 10.12 Property test: nhãn xác minh hợp lệ
    - **Property 34: Nhãn xác minh luôn thuộc tập hợp lệ**
    - **Validates: Requirements 8.1**

  - [x]* 10.13 Property test: xác minh lỗi/timeout → "chưa xác minh"
    - **Property 35: Xác minh lỗi/timeout suy biến an toàn về "chưa xác minh"**
    - **Validates: Requirements 8.2, 8.3**

  - [x]* 10.14 Property test: tổng hợp lỗi/timeout trả chunk gốc
    - **Property 36: Tổng hợp lỗi/timeout trả chunk gốc làm dự phòng**
    - **Validates: Requirements 8.4, 16.4**

  - [x] 10.15 answerOverview + gợi ý + áp MauPrompt
    - `answerOverview` dựa trên TomTatTaiLieu + outline các TaiLieu có quyền, kèm ≥1 TrichDan; gợi ý sinh từ tài liệu thực, không có tài liệu → báo "chưa có tài liệu", không gọi LLM; áp MauPrompt theo vai trò + giữ INVARIANT_SAFETY_CONSTRAINTS; chuẩn hóa trống dùng provider xác minh
    - _Requirements: 13.4, 15.3, 15.4, 15.5, 16.1, 16.2, 16.3, 16.5, 20.1, 20.2, 20.3_

  - [x]* 10.16 Property test: trả lời tổng quan kèm trích dẫn
    - **Property 41: Trả lời tổng quan dựa trên tóm tắt/outline kèm trích dẫn**
    - **Validates: Requirements 16.1, 16.2**

  - [x]* 10.17 Property test: gợi ý từ tài liệu thực / báo trống
    - **Property 42: Gợi ý sinh từ tài liệu thực; không có tài liệu thì báo trống**
    - **Validates: Requirements 15.4, 15.5, 16.5**

  - [x]* 10.18 Property test: MauPrompt giữ ràng buộc bất biến
    - **Property 50: MauPrompt giữ ràng buộc an toàn bất biến**
    - **Validates: Requirements 20.1, 20.2, 20.3**

  - [x]* 10.19 Property test: vai trò chuẩn hóa trống dùng provider xác minh
    - **Property 51: Vai trò chuẩn hóa trống dùng provider xác minh**
    - **Validates: Requirements 13.4**

- [x] 11. HistoryService
  - [x] 11.1 saveTurn / listHistory / deleteTurn / markStaleCitations
    - Lưu cặp câu hỏi-trả lời gắn TaiKhoan + KhongGianTaiLieu + timestamp; liệt kê chỉ của chính mình, giảm dần, ≤50; xóa chỉ mục của mình; cắt lại tài liệu → đánh dấu TrichDan cũ không còn khả dụng; lưu lỗi vẫn trả câu trả lời + cảnh báo, không tạo mục dở
    - _Requirements: 3.5, 3.7, 3.8, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 9.7, 9.8_

  - [x]* 11.2 Property test: lịch sử cô lập, đúng thứ tự, giới hạn
    - **Property 17: Lịch sử trò chuyện cô lập, đúng thứ tự và giới hạn**
    - **Validates: Requirements 3.5, 3.7, 3.8, 9.3, 9.6, 9.7**

  - [x]* 11.3 Property test: lưu lịch sử round-trip, lỗi không tạo mục dở
    - **Property 37: Lưu lịch sử là round-trip; lưu lỗi không tạo mục dở**
    - **Validates: Requirements 9.1, 9.2**

  - [x]* 11.4 Property test: cắt lại đánh dấu TrichDan cũ
    - **Property 38: Cắt lại tài liệu đánh dấu TrichDan cũ là không còn khả dụng**
    - **Validates: Requirements 9.8**

- [x] 12. ConfigService và AdminService
  - [x] 12.1 ConfigService cấu hình truy xuất (update/reset)
    - `updateRetrievalConfig` (ngưỡng ∈ [0,1], dưới ≤ trên, k + trọng số hợp lệ, cần GHI; ngoài range → từ chối giữ nguyên); `resetRetrievalConfig`; áp dụng cho truy vấn kế tiếp
    - _Requirements: 19.1, 19.2, 19.3, 19.4, 19.5_

  - [x]* 12.2 Property test: hợp lệ hóa + áp dụng CauHinhTruyXuat
    - **Property 48: Hợp lệ hóa và áp dụng CauHinhTruyXuat**
    - **Validates: Requirements 19.1, 19.2, 19.3, 19.4**

  - [x] 12.3 ConfigService MauPrompt + giới hạn vận hành
    - `updatePromptTemplate`/`resetPromptTemplate` (chỉ QUAN_TRI, áp INVARIANT_SAFETY_CONSTRAINTS); `updateOperationalLimits` (llm_timeout/sessionTtl/maxFileSize trong range, áp runtime); ngoài range → từ chối giữ nguyên
    - _Requirements: 20.1, 20.2, 23.1, 23.2, 23.3_

  - [x]* 12.4 Property test: hợp lệ hóa + áp dụng giới hạn vận hành
    - **Property 49: Hợp lệ hóa và áp dụng giới hạn vận hành**
    - **Validates: Requirements 23.1, 23.2, 23.3**

  - [x] 12.5 AdminService quản lý tài khoản
    - `listAccounts`, `disableAccount` (không tự vô hiệu mình; thu hồi phiên), `enableAccount`; thao tác tới tài khoản không tồn tại → 404
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [x]* 12.6 Unit test: tự vô hiệu hóa bị từ chối + tài khoản không tồn tại
    - _Requirements: 10.4, 10.5_

- [x] 13. Khớp nối API routes và main
  - [x] 13.1 Routes xác thực
    - `api/routes/auth.py`: register/login/logout/refresh/password change/reset-request/reset; `DELETE /api/account`
    - _Requirements: 1.1, 2.1, 2.8, 25.1, 25.2, 25.4, 25.5, 25.6_

  - [x] 13.2 Routes workspaces + shares + retrieval-config
    - `api/routes/workspaces.py`: GET/POST/PATCH/DELETE workspaces, POST/DELETE shares, GET/PUT retrieval-config (gắn require_workspace_access)
    - _Requirements: 3.1, 4.3, 4.5, 11.1, 11.6, 19.1_

  - [x] 13.3 Routes documents
    - `api/routes/documents.py`: upload (preview), list (phân trang), delete, GET/PUT chunks, commit, rechunk, reset
    - _Requirements: 5.1, 5.7, 5.8, 18.1, 18.3, 18.6, 18.7_

  - [x] 13.4 Routes query + history
    - `api/routes/query.py` (gắn rate limit), `api/routes/history.py`
    - _Requirements: 6.1, 9.3, 9.6, 24.2_

  - [x] 13.5 Routes admin + account api-keys
    - `api/routes/admin.py` (users disable/enable, quota, prompts, limits); `GET/PUT/DELETE /api/account/api-keys`
    - _Requirements: 10.1, 12.5, 20.1, 22.1, 23.1_

  - [x] 13.6 Khớp nối main.py
    - Đăng ký mọi router + middleware (auth, correlationId, log, rate limit, CORS, error handler) + DI lifespan; phục vụ frontend dist (single-service)
    - _Requirements: 2.6, 14.1, 14.2, 24.1_

  - [x]* 13.7 Integration tests endpoint chính
    - Luồng đăng ký→đăng nhập→tạo không gian→upload→commit→query→history; kiểm 401/403/404 cho cô lập dữ liệu
    - _Requirements: 2.6, 3.2, 3.3, 6.2_

- [x] 14. Checkpoint — Backend
  - Đảm bảo toàn bộ test backend pass, hỏi người dùng nếu có vướng mắc.
