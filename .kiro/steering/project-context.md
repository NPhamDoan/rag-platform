# Project Context — Vietnam Law RAG

> Tài liệu ngữ cảnh để đọc nhanh mỗi phiên làm việc, KHÔNG cần đọc lại toàn bộ
> requirements + source. Cập nhật khi kiến trúc/tính năng thay đổi.
>
> Giải thích cơ chế RAG ở mức khái niệm (embedding, BM25, RRF, ngưỡng, xác minh,
> enrich, phần cứng LLM): xem `docs/giai-thich-he-thong-rag.md`.

## 1. Tổng quan

Hệ thống RAG tra cứu **luật Việt Nam**: người dùng mô tả tình huống / đặt câu hỏi
→ hệ thống truy xuất điều luật liên quan → LLM tổng hợp **giải pháp + căn cứ pháp lý**
kèm **trích dẫn nguồn**. Bám chặt văn bản (strict grounding), không bịa luật.

- **Backend:** Python + FastAPI (thư mục `backend/`, package `app`)
- **Frontend:** React 19 + Vite + TypeScript + Tailwind v4 (`frontend/`)
- **Vector DB:** ChromaDB (persistent, `data/chroma/`)
- **Embedding:** HuggingFace `intfloat/multilingual-e5-large` (local, 1024 chiều)
- **LLM:** Groq tổng hợp (mặc định `llama-3.1-8b-instant` để bền quota; có thể đổi `llama-3.3-70b-versatile` chất lượng cao hơn nếu giảm SYNTHESIS_MAX_CHUNKS) + Gemini `gemini-2.0-flash` (xác minh)
- **Chunk:** 1 Điều = 1 chunk; Điều quá dài hoặc tài liệu không có Điều (thông tư/phụ lục) → cắt theo kích thước (~1200 ký tự, overlap 150)

## 2. Luồng xử lý truy vấn (QueryPipeline.process_query)

1. **Help-intent**: nếu hỏi về chính hệ thống ("có thể hỏi gì", "list...") → trả
   danh sách văn bản + gợi ý, KHÔNG qua RAG.
1b. **Toàn văn Điều** ("toàn văn/full/nội dung Điều N", kèm số hiệu nếu có) →
   `get_dieu()` gom mọi chunk cùng `dieu`, ghép nguyên văn, trả thẳng (KHÔNG qua LLM).
   Trùng ở nhiều văn bản mà thiếu số hiệu → liệt kê để người dùng chọn.
2. **Chuẩn hóa câu hỏi**: nếu gõ KHÔNG dấu → gọi LLM thêm dấu (mặc định dùng `verify_llm`=Gemini; có thể tách riêng qua `NORMALIZE_LLM_*` trong .env). Câu có dấu thì bỏ qua. Guard: chỉ nhận kết quả nếu CÙNG bộ từ sau khi bỏ dấu (chỉ thêm dấu, không đổi từ), lệch → giữ câu gốc.
3. **Embed** câu hỏi → **Hybrid search** (vector cosine + BM25 từ khóa, hợp nhất RRF, k=8).
4. **Lọc ngưỡng**: max score < 0.3 → "không tìm thấy"; < 0.5 → "chưa đủ liên quan"
   (cả hai KHÔNG gọi LLM tổng hợp).
5. **Tổng hợp** (Groq): prompt đánh số chunk [1..N], LLM chèn marker [n] inline.
6. **Cross-verification** (Gemini): trả "đã xác minh" / "có mâu thuẫn" / "chưa xác minh"
   (hết quota Gemini → "chưa xác minh", không lỗi).
7. **Fallback**: LLM lỗi/timeout → trả chunk gốc (`chunksGoc`).

## 3. Bản đồ source — Backend (`backend/app/`)

| File | Trách nhiệm |
|---|---|
| `main.py` | Tạo FastAPI app, lifespan (DI), CORS, middleware log request, **phục vụ frontend dist (single-service)**, đăng ký routers |
| `config.py` | `Settings` (pydantic-settings) đọc `.env`; `chroma_persist_path` neo theo gốc dự án; cấu hình LLM/embedding/ngưỡng/auth/log |
| `logging_config.py` | `setup_logging()` — console + file (rotate), ép UTF-8 |
| `api/auth.py` | Token HMAC (create/verify) + dependency `require_admin` |
| `api/dependencies.py` | DI container: get/set query_pipeline, document_pipeline, vector_store |
| `api/middleware/error_handler.py` | Global handler → 400 (validation) / 500 (correlationId + log stack) |
| `api/routes/query.py` | `POST /api/query` (công khai) — validate cauHoi 1..1000 |
| `api/routes/documents.py` | `POST` (admin) / `GET` (công khai) / `DELETE {soHieu:path}` (admin) |
| `api/routes/auth.py` | `POST /api/auth/login` → token admin |
| `chunking/vietnamese_law_chunker.py` | Cắt theo Điều (`DIEU_BOUNDARY_PATTERN = ^[ \t]*Điều\s+(\d+)\s*[.:]`, bắt buộc dấu ./: sau số để tránh cắt nhầm tham chiếu). KHÔNG có Điều (thông tư/phụ lục) hoặc Điều > `max_chunk_size` → cắt theo size + overlap (`_split_by_size`, cấu hình qua constructor) |
| `storage/vector_store.py` | ChromaDB wrapper. `search(query_vector, k, query_text=None)`: có query_text → **hybrid (BM25 + RRF)**; `add_chunks` (dedup ID trùng), `delete_by_so_hieu` (rollback), `list_documents` (phân trang), `get_dieu(so_dieu, so_hieu=None)` (gom toàn văn 1 Điều theo metadata, không cần embedding). BM25 tự viết + tokenizer bỏ dấu/giữ số |
| `pipelines/document_pipeline.py` | parse (PyMuPDF/txt) → chunk → embed → store; giới hạn 50MB; `replace=True` để ghi đè; chống nạp PDF scan (0 chunk). Nhận `enricher` (tùy chọn): embed text ĐÃ enrich (gốc + tóm tắt/từ khóa/câu hỏi) nhưng lưu text gốc |
| `pipelines/query_pipeline.py` | Toàn bộ luồng mục 2: normalize, hybrid search, gating, synthesis, verification, fallback, help-intent |
| `prompts/system_prompts.py` | `SYNTHESIS_SYSTEM_PROMPT` (4 mục: Phân tích/Giải pháp/Căn cứ/Lưu ý + chèn [n]), `VERIFICATION_SYSTEM_PROMPT`, `NORMALIZE_SYSTEM_PROMPT` |
| `providers/` | **Registry tự-đăng-ký** (`registry.py`): mỗi class LLM provider tự đăng ký bằng `@register_llm("ten")` + (tùy chọn) classmethod `build_from(settings, model, api_key, timeout)` để tự quyết dùng API key hay URL. `provider_factory.py` chỉ tra registry — **THÊM PROVIDER LLM MỚI = tạo file `*_provider.py` + decorator, KHÔNG sửa factory/core** (auto-discover mọi `*_provider.py`). Đổi `groq \| gemini \| ollama` qua `.env` (PRIMARY/VERIFY/NORMALIZE/ENRICH_LLM_PROVIDER); `gemini_provider.py`, `groq_provider.py` (API key), `ollama_provider.py` (URL, có `build_from`), `huggingface_embedding.py` (prefix e5), interface `llm_provider.py` / `embedding_provider.py` |
| `models/schemas.py` | Pydantic: `DocumentMetadataInput`, `IndexingResult`, `TrichDan`, `KetQuaTraLoi`, `PaginatedDocumentResponse` |

## 4. Bản đồ source — Frontend (`frontend/src/`)

| File | Trách nhiệm |
|---|---|
| `api/client.ts` | axios; baseURL = `VITE_API_BASE_URL ?? localhost:8000` (rỗng="" → tương đối); interceptor gắn Bearer token + xóa token khi 401 |
| `auth/AuthContext.tsx` | Quản lý token (localStorage), `login`/`logout`/`isAdmin` |
| `auth/RequireAdmin.tsx` | Chặn route admin → chưa đăng nhập về `/login` |
| `App.tsx` | Router: `/` ChatPage, `/login` LoginPage, `/admin` (guarded) AdminPage; bọc AuthProvider |
| `components/common/AppLayout.tsx` | Header + nav (ẩn "Quản lý" khi chưa đăng nhập, nút đăng nhập/đăng xuất) |
| `pages/ChatPage.tsx` | Chat: trống → `WelcomeSuggestions`; có tin → `MessageList` |
| `pages/AdminPage.tsx` | `DocumentUpload` + `DocumentList` |
| `pages/LoginPage.tsx` | Form đăng nhập admin |
| `hooks/useQuery.ts` | Gọi `/api/query`, lịch sử cap 50 tin |
| `hooks/useDocuments.ts` | upload (timeout 0 + onUploadProgress) / list / delete |
| `components/chat/MarkdownWithCitations.tsx` | Render markdown, biến `[n]` thành nút bấm → mở overlay điều luật |
| `components/chat/MessageBubble.tsx` | Hiển thị KetQuaTraLoi: markdown + badge + trích dẫn + overlay |
| `components/chat/WelcomeSuggestions.tsx` | Màn chào: danh sách luật + câu hỏi gợi ý |
| `components/chat/{ChatInput,MessageList,TrichDanItem}.tsx` | Nhập (500 ký tự), danh sách tin, chip trích dẫn |
| `components/common/{ConfidenceBadge,VerificationBadge,DieuOverlay,LoadingIndicator}.tsx` | Badge độ tin cậy/xác minh, overlay nội dung điều, loading |
| `types/index.ts` | `TrichDan`, `KetQuaTraLoi`, `Message`, `DocumentMetadata` |

## 5. API

- `POST /api/query` `{cauHoi}` → `KetQuaTraLoi` — công khai
- `POST /api/documents` (multipart, **admin**) — 409 nếu trùng soHieu
- `GET /api/documents?page&pageSize` — công khai (chat/welcome dùng)
- `DELETE /api/documents/{soHieu:path}` — **admin**
- `POST /api/auth/login` `{username,password}` → `{token, role}`
- Auth: header `Authorization: Bearer <token>`; chỉ admin đăng nhập, chat công khai.

## 6. Quy ước & ràng buộc

- **Đặt tên:** entity/field tiếng Việt không dấu (soHieu, tenVanBan); verb/method tiếng Anh (createBook); UI label tiếng Việt có dấu.
- **KHÔNG bịa nội dung luật** — chỉ dùng văn bản thật trong `data/seed/` (manifest.json).
- Windows + Application Control: chạy `python -m uvicorn`, `python -m pytest`, `python -m pip` (tránh .exe bị chặn).
- Test: `cd backend && python -m pytest tests/ -q` (hiện ~179 test pass).
- Ràng buộc: model embedding ~2GB RAM → Render free (512MB) không chạy; HF Spaces free (16GB) chạy được. Gemini free tier dễ hết quota (đã chuyển primary sang Groq).
- **Config `.env`:** `config.py` neo `env_file` TUYỆT ĐỐI vào `backend/.env` (theo vị trí file, không theo cwd) → chạy từ `backend/` hay từ gốc (seed_data.py, enrich_preview.py) đều đọc đúng cùng `.env`. Đừng đổi lại thành `".env"` tương đối (sẽ khiến script chạy từ gốc rơi về giá trị mặc định).
- **Đổi LLM:** mọi vai trò đổi qua `.env`, KHÔNG sửa code — `PRIMARY_*` (tổng hợp), `VERIFY_*` (xác minh), `NORMALIZE_*` (thêm dấu; trống = dùng chung VERIFY), `ENRICH_*` (+ `ENRICH_API_KEY`). Thêm provider mới = file `*_provider.py` + `@register_llm`. Chi tiết: `docs/giai-thich-he-thong-rag.md` mục 14.

## 7. Chạy / Deploy

- Lần đầu: `setup.bat` (cài deps + .env) → điền API key → `seed.bat` (nạp luật).
- Chạy: `run_all.bat` (backend `python -m uvicorn app.main:app`, frontend `npm run dev`).
- Dọn source: `clean.bat`. Deploy 1 service: `Dockerfile` + `render.yaml` (xem README mục 9).
- Tài khoản admin mặc định: `admin` / `admin123` (đổi trong `backend/.env`).

## 8. Công cụ kiểm tra (thư mục gốc)

- `enrich_preview.py` — xem CHẤT LƯỢNG enrich (LLM sinh tóm tắt/từ khóa/câu hỏi gì cho vài chunk), chạy TRƯỚC seed, vài giây, cần Ollama. Vd: `python enrich_preview.py --count 3`.
- `check_retrieval.py` — kiểm tra HIỆU QUẢ retrieval SAU seed: chạy bộ câu hỏi đã biết (gồm ca từng lỗi: tốc độ khu dân cư 60/40km/h, thừa kế Điều 650), in chunk tìm được + điểm + chấm ✓/✗. Không gọi LLM tổng hợp. Vd: `python check_retrieval.py`.
- DBeaver: mở `data/chroma/chroma.sqlite3` (driver SQLite) chỉ xem được metadata + text gốc (`chroma:document`); vector nằm ở file HNSW nhị phân riêng. ChromaDB = vector DB, SQLite chỉ là kho metadata.
