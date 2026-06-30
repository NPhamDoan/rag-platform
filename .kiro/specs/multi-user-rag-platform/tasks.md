# Implementation Plan: Multi-User RAG Platform

## Overview

Kế hoạch triển khai nền tảng RAG đa người dùng, đa lĩnh vực, tổng quát hóa từ hệ thống Vietnam Law RAG. Công việc được chia thành **bốn nhóm** và **tách thành các file riêng** theo thứ tự:

1. **Backend (BE)** — FastAPI, package `app` → `tasks-backend.md` (task 1–14)
2. **Shared (gói TypeScript dùng chung)** — types + API client contract → `tasks-shared.md` (task 15)
3. **Web** — React 19 + Vite → `tasks-web.md` (task 16)
4. **Mobile** — React Native + Expo → `tasks-mobile.md` (task 17–20: gồm checkpoint clients, kiểm thử nhất quán Web/Mobile, checkpoint cuối)

Mỗi bước xây dựng tăng dần và kết nối vào các bước trước, không để code mồ côi.

Quy ước áp dụng xuyên suốt (theo steering):
- Đặt tên: entity/field tiếng Việt không dấu (`taiKhoan`, `khongGianId`); verb/method tiếng Anh (`createWorkspace`, `verifyToken`); UI label tiếng Việt có dấu.
- Logging tập trung từ task đầu tiên; mọi module ghi log qua logger chung, che trường nhạy cảm, không nuốt lỗi im lặng.
- Property-based test (PBT): backend dùng **Hypothesis**, gói shared dùng **fast-check**; mỗi correctness property hiện thực bằng **đúng một** PBT chạy tối thiểu 100 vòng, gắn comment `# Feature: multi-user-rag-platform, Property {n}: ...`.
- LLM/Embedding provider được **mock** trong PBT.

## Tasks

Danh sách task được tách thành các file riêng theo nhóm (mở từng file để chạy task):

| Thứ tự | Nhóm | File | Task |
|--------|------|------|------|
| 1 | Backend (BE) | [tasks-backend.md](tasks-backend.md) | 1–14 |
| 2 | Shared (TypeScript) | [tasks-shared.md](tasks-shared.md) | 15 |
| 3 | Web | [tasks-web.md](tasks-web.md) | 16 |
| 4 | Mobile | [tasks-mobile.md](tasks-mobile.md) | 17–20 |

## Notes

- Các task gắn `*` là tùy chọn (test) và có thể bỏ qua khi cần MVP nhanh; task triển khai lõi không bao giờ gắn `*`.
- Mỗi correctness property được hiện thực bằng đúng một property-based test đặt gần phần triển khai để bắt lỗi sớm.
- PBT backend dùng Hypothesis (≥100 vòng), gói shared dùng fast-check; LLM/Embedding provider được mock trong PBT.
- Phần đặc thù Mobile (điều hướng, secure-store, document-picker) kiểm bằng test thành phần/E2E của Expo, không phải PBT.
- Logging tập trung được thiết lập ngay ở task 1 (backend) và task 17.2 (mobile) theo quy ước dự án.
- Checkpoint đảm bảo kiểm chứng tăng dần ở các điểm dừng hợp lý.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "15.1", "16.1", "17.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4", "1.5", "15.2", "15.3", "17.2"] },
    { "id": 2, "tasks": ["1.6", "1.7", "1.8", "2.1", "2.2", "15.4", "16.2", "17.3", "17.4"] },
    { "id": 3, "tasks": ["3.1", "3.3", "4.1", "7.1", "9.1", "16.3", "17.5", "17.6"] },
    { "id": 4, "tasks": ["3.2", "3.4", "3.5", "3.8", "4.2", "4.3", "5.1", "6.1", "7.2", "9.2", "9.3", "9.4", "9.5", "16.4", "17.7", "17.8"] },
    { "id": 5, "tasks": ["3.6", "3.7", "3.9", "3.10", "3.11", "3.12", "3.13", "5.2", "5.3", "5.4", "5.5", "6.2", "6.3", "6.4", "7.3", "7.4", "7.5", "8.1", "10.1", "12.1", "12.3", "12.5", "17.9"] },
    { "id": 6, "tasks": ["3.14", "3.15", "3.16", "3.17", "3.18", "5.6", "5.7", "6.5", "8.2", "8.3", "8.4", "8.5", "8.10", "10.2", "10.3", "10.4", "10.5", "10.6", "11.1", "12.2", "12.4", "12.6", "13.1", "13.2"] },
    { "id": 7, "tasks": ["8.6", "8.7", "8.8", "8.9", "8.11", "8.12", "10.7", "10.8", "10.9", "10.10", "10.15", "11.2", "11.3", "11.4", "13.3", "13.5"] },
    { "id": 8, "tasks": ["10.11", "10.12", "10.13", "10.14", "10.16", "10.17", "10.18", "10.19", "13.4"] },
    { "id": 9, "tasks": ["13.6"] },
    { "id": 10, "tasks": ["13.7"] },
    { "id": 11, "tasks": ["19.1"] }
  ]
}
```
