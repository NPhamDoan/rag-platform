# Implementation Plan — Mobile (React Native + Expo)

> Nhóm 4/4 của Multi-User RAG Platform. Xem `tasks.md` cho tổng quan, Notes và Task Dependency Graph chung.
> Phụ thuộc gói Shared (`tasks-shared.md`). Bao gồm checkpoint clients + kiểm thử nhất quán Web/Mobile + checkpoint cuối.

## Quy ước (theo steering)
- UI label tiếng Việt có dấu; entity/field tiếng Việt không dấu; verb/method tiếng Anh.
- Logging tập trung phía Mobile (task 17.2); cấm console trực tiếp.
- Phần đặc thù Mobile kiểm bằng test thành phần/E2E của Expo, không phải PBT.

---

## Tasks

- [ ] 17. Ứng dụng Mobile Expo
  - [ ] 17.1 Scaffold dự án Expo + phụ thuộc
    - Tạo `mobile/` (Expo managed + expo-router); cài expo-router, expo-secure-store, expo-document-picker, axios; cấu hình `app.json`/`app.config.ts` + `EXPO_PUBLIC_API_BASE_URL`
    - _Requirements: 26.1, 26.9_

  - [ ] 17.2 Module logger tập trung phía Mobile
    - `src/lib/logger.ts`: một nguồn cấu hình duy nhất, đúng 4 mức (ERROR/WARN/INFO/DEBUG), lọc theo môi trường (prod ≥ INFO, dev ≥ DEBUG), trường bắt buộc (timestamp+timezone/level/nguồn/message), che token/mật khẩu/khóa API ở mọi cấp lồng, kênh dự phòng khi init lỗi; cấm console trực tiếp
    - _Requirements: 30.1, 30.2, 30.3, 30.4, 30.5, 30.6, 30.7_

  - [ ]* 17.3 Unit test: logger che dữ liệu nhạy cảm + đủ trường + không nuốt lỗi
    - _Requirements: 30.2, 30.5, 30.6_

  - [ ] 17.4 AuthContext secure-store + xử lý 401
    - `AuthContext` đọc/ghi token qua expo-secure-store (async); ghi token thất bại → giữ chưa đăng nhập + báo lỗi; nhận 401 → xóa token ≤2s + điều hướng login; xóa thất bại vẫn điều hướng + chặn token; đăng xuất xóa token; không log token
    - _Requirements: 26.2, 26.10, 27.1, 27.2, 27.3, 27.4, 27.5, 27.6, 27.7_

  - [ ]* 17.5 Unit test: 401 xóa token + điều hướng; secure-store fail
    - _Requirements: 27.2, 27.3, 27.5_

  - [ ] 17.6 Điều hướng + guard quyền
    - expo-router: nhóm `(auth)` và `(app)`; guard `(app)` yêu cầu token; màn admin yêu cầu QUAN_TRI, ngược lại điều hướng về danh sách không gian
    - _Requirements: 26.7, 26.8_

  - [ ] 17.7 Màn hình Mobile
    - Login/register, danh sách KhongGianTaiLieu (≤5s, lỗi → thử lại), chat hỏi đáp (1–1000 ký tự), lịch sử của chính mình, màn quản trị; validate form từ chối gửi khi đầu vào không hợp lệ
    - _Requirements: 26.3, 26.5, 26.6, 26.7, 26.11, 26.12_

  - [ ] 17.8 Tải tệp qua document-picker
    - Mở bộ chọn tệp native (đơn tệp) → gửi multipart tới endpoint hiện có; hủy không gửi request; tiến trình 0–100%; thành công báo + kết thúc tiến trình; lỗi mạng → báo + cho thử lại; giới hạn kích thước/định dạng do backend thực thi
    - _Requirements: 26.4, 28.1, 28.2, 28.3, 28.4, 28.5, 28.6, 28.7_

  - [ ]* 17.9 Test thành phần: hủy/tiến trình/lỗi tải lên
    - _Requirements: 28.3, 28.5, 28.7_

- [ ] 18. Checkpoint — Clients
  - Đảm bảo test gói shared, Web và Mobile pass, hỏi người dùng nếu có vướng mắc.

- [ ] 19. Kiểm thử nhất quán hành vi Web/Mobile
  - [ ]* 19.1 Integration test: cùng đầu vào → cùng kết quả qua hợp đồng dùng chung
    - Kiểm cùng endpoint + đầu vào trả cùng trạng thái/cấu trúc DTO/giá trị nghiệp vụ (KetQuaTraLoi, nhãn, TrichDan trùng số lượng/thứ tự/nội dung), cùng nhận diện loại lỗi
    - _Requirements: 29.1, 29.2, 29.3, 29.6_

- [ ] 20. Checkpoint cuối
  - Đảm bảo tất cả test pass, hỏi người dùng nếu có vướng mắc.
