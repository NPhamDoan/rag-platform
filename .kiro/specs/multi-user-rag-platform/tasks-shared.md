# Implementation Plan — Shared (gói TypeScript dùng chung)

> Nhóm 2/4 của Multi-User RAG Platform. Xem `tasks.md` cho tổng quan, Notes và Task Dependency Graph chung.

## Quy ước (theo steering)
- Đặt tên field tiếng Việt không dấu; verb/method tiếng Anh.
- Property-based test (PBT) gói shared dùng **fast-check**; mỗi correctness property hiện thực bằng **đúng một** PBT chạy tối thiểu 100 vòng.

---

## Tasks

- [x] 15. Gói shared: types + API client contract
  - [x] 15.1 Thiết lập gói shared + types
    - Tạo `shared/` (tsconfig, build/package config); định nghĩa types phản chiếu DTO backend (`TrichDan`, `KetQuaTraLoi`, `Message`, `DocumentMetadata`, `KhongGianTaiLieu`, `TaiLieu`, `ChunkPreview`, `RetrievalConfigInput`, `KhoaApiMasked`...), đặt tên field tiếng Việt không dấu
    - _Requirements: 29.1_

  - [x] 15.2 createApiClient contract (Bearer + 401 + ánh xạ lỗi)
    - Factory `createApiClient({ baseURL, getToken, onUnauthorized })`: định nghĩa mọi endpoint (auth/workspaces/documents/query/history/admin/api-keys), gắn Bearer token, xử lý 401, ánh xạ loại lỗi → thông điệp; không phụ thuộc nơi lưu token
    - _Requirements: 29.1, 29.2, 29.6_

  - [x] 15.3 Logic nghiệp vụ thuần dùng chung
    - Ánh xạ marker `[n]` ↔ TrichDan (marker ngoài [1,N] → văn bản thường, không link hỏng), cap lịch sử 50 mục, validate độ dài câu hỏi phía client
    - _Requirements: 29.3, 29.4, 29.5_

  - [x]* 15.4 fast-check test: ánh xạ marker nhất quán
    - **Property 33: Marker trích dẫn nằm trong 1..N và song ánh với danh sách TrichDan** (kiểm tại tầng client dùng chung; nhất quán Web/Mobile)
    - **Validates: Requirements 29.4, 29.5**
