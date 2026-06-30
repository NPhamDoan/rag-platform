# Implementation Plan — Web (React + Vite)

> Nhóm 3/4 của Multi-User RAG Platform. Xem `tasks.md` cho tổng quan, Notes và Task Dependency Graph chung.
> Phụ thuộc gói Shared (`tasks-shared.md`).

## Quy ước (theo steering)
- UI label tiếng Việt có dấu; entity/field tiếng Việt không dấu; verb/method tiếng Anh.
- Tiêu thụ gói `shared` cho types + API client + logic citations.

---

## Tasks

- [ ] 16. Web tiêu thụ gói shared
  - [ ] 16.1 Thiết lập/đối chiếu dự án web + tích hợp shared
    - Đảm bảo `frontend/` (React 19 + Vite + TS + Tailwind v4) tồn tại; thêm phụ thuộc gói `shared`; cấu hình `VITE_API_BASE_URL`
    - _Requirements: 29.1_

  - [ ] 16.2 AuthContext (localStorage) + api client + RequireAdmin
    - `AuthContext` lưu token localStorage; khởi tạo `createApiClient` với getter localStorage + điều hướng router khi 401; guard `RequireAdmin`
    - _Requirements: 29.2, 29.6_

  - [ ] 16.3 Màn hình web
    - Auth (đăng ký/đăng nhập), danh sách KhongGianTaiLieu, tải lên + xem trước + sửa Chunk, chat hỏi đáp (render markdown + citations qua logic shared), bảng quản trị
    - _Requirements: 18.1, 18.3, 16.1, 10.1_

  - [ ]* 16.4 Component test: render marker không hợp lệ thành văn bản thường
    - _Requirements: 29.5_
