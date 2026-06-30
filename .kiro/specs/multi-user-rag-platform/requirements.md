# Requirements Document

## Introduction

Tài liệu này mô tả yêu cầu cho một nền tảng RAG (Retrieval-Augmented Generation) **đa người dùng, đa lĩnh vực** (general-purpose), tổng quát hóa từ hệ thống RAG tra cứu luật Việt Nam hiện có. Mục tiêu là cho phép **bất kỳ người dùng nào tự đăng ký tài khoản**, tạo và quản lý các **không gian tài liệu** (workspace/collection) của riêng mình, tải tài liệu lên và **trò chuyện hỏi đáp dựa trên chính tài liệu của họ** — với dữ liệu được **cô lập** giữa các người dùng.

Nền tảng kế thừa các đặc tính cốt lõi của hệ thống gốc nhưng **bỏ mọi giả định gắn với lĩnh vực luật**: bám sát ngữ cảnh tài liệu (strict grounding, không bịa nội dung), trích dẫn nguồn nội tuyến `[n]`, xác minh chéo (cross-verification), tìm kiếm lai (vector + BM25 hợp nhất bằng RRF), lọc theo ngưỡng điểm, và cơ chế dự phòng khi LLM lỗi.

Phạm vi của tài liệu này tập trung vào HÀNH VI hệ thống (cái gì), không quy định chi tiết kỹ thuật triển khai (cách làm) — phần đó để dành cho tài liệu thiết kế.

Nền tảng được tổ chức thành **ba tầng** chạy song song và **dùng chung một backend FastAPI** qua REST API: (1) Backend (về cơ bản không đổi), (2) Web (client web React hiện có, giữ nguyên), và (3) Mobile (ứng dụng native React Native + Expo, mới, cho iOS và Android — không phải webview/iframe của web). Web và Mobile là hai client ngang hàng, cùng tiêu thụ một hợp đồng API và dùng chung kiểu dữ liệu TypeScript cùng logic gọi API; phần đặc thù thiết bị (điều hướng, màn hình native, lưu token an toàn trên máy, chọn tệp từ thiết bị) được tách riêng cho mỗi client.

### Bối cảnh kỹ thuật (tham chiếu, không ràng buộc thiết kế)

- Backend: Python + FastAPI (package `app`); Frontend Web: React 19 + Vite + TypeScript + Tailwind v4
- Mobile: React Native + Expo (iOS + Android), điều hướng qua expo-router/React Navigation, lưu token qua expo-secure-store, chọn tệp qua expo-document-picker; tiêu thụ cùng REST API, không thêm endpoint backend
- Vector DB: ChromaDB (persistent); Embedding: HuggingFace multilingual-e5-large (local, 1024 chiều)
- LLM: Groq (tổng hợp) + Gemini (xác minh) qua registry tự-đăng-ký provider
- Quy ước đặt tên: entity/field tiếng Việt không dấu; verb/method tiếng Anh; nhãn UI tiếng Việt có dấu

## Glossary

- **He_Thong**: Toàn bộ nền tảng RAG đa người dùng, đa lĩnh vực được mô tả trong tài liệu này.
- **Auth_Service**: Thành phần phụ trách đăng ký, đăng nhập, đăng xuất, xác thực và quản lý phiên/token người dùng.
- **TaiKhoan**: Bản ghi đại diện một người dùng đã đăng ký, gồm định danh duy nhất, email, tên đăng nhập, mật khẩu đã băm và vai trò.
- **VaiTro**: Phân loại quyền của một TaiKhoan; giá trị thuộc tập { NGUOI_DUNG, QUAN_TRI }.
- **KhongGianTaiLieu**: Một không gian tài liệu (workspace/collection) thuộc sở hữu của một TaiKhoan, chứa tập tài liệu và phục vụ truy vấn độc lập. Mỗi KhongGianTaiLieu có định danh duy nhất, tên, mô tả và chủ sở hữu.
- **TaiLieu**: Một tài liệu (tệp) do người dùng tải lên, thuộc về đúng một KhongGianTaiLieu, gồm metadata (tên, định dạng, kích thước) và nội dung đã được tách đoạn (chunk).
- **Chunk**: Một đoạn văn bản được tách ra từ TaiLieu để embed và lưu trữ phục vụ truy xuất.
- **Document_Pipeline**: Thành phần xử lý tài liệu: phân tích (parse) → tách đoạn (chunk) → tạo embedding → lưu trữ.
- **Query_Pipeline**: Thành phần xử lý truy vấn: chuẩn hóa câu hỏi → tìm kiếm lai → lọc ngưỡng → tổng hợp câu trả lời → xác minh chéo → dự phòng.
- **Vector_Store**: Kho lưu trữ vector và metadata của các Chunk, hỗ trợ tìm kiếm lai (vector + BM25) và phân vùng dữ liệu theo KhongGianTaiLieu.
- **LLM_Provider**: Nhà cung cấp mô hình ngôn ngữ lớn dùng cho tổng hợp, xác minh hoặc chuẩn hóa, đăng ký qua registry.
- **TrichDan**: Tham chiếu tới Chunk nguồn được dùng để tạo câu trả lời, hiển thị dưới dạng marker `[n]` nội tuyến.
- **LichSuTroChuyen**: Tập các cặp câu hỏi - câu trả lời của một TaiKhoan trong một KhongGianTaiLieu, lưu theo thứ tự thời gian.
- **HanMuc**: Giới hạn tài nguyên áp cho một TaiKhoan (ví dụ: số KhongGianTaiLieu, số TaiLieu, tổng dung lượng).
- **PhienXacThuc**: Trạng thái đăng nhập hợp lệ của một TaiKhoan, gắn với một token có thời hạn.
- **Diem_Lien_Quan**: Điểm số biểu thị mức độ liên quan của Chunk truy xuất được với câu hỏi, dùng để lọc ngưỡng.
- **ChienLuocChunk**: Thuật toán tách đoạn (chunking strategy) một TaiLieu thành các Chunk, được đăng ký qua registry; ví dụ recursive, structure-aware/markdown, page, semantic, vietnamese-law. Có một chế độ "Tự động" tự nhận diện loại tài liệu để chọn chiến lược phù hợp.
- **TomTatTaiLieu**: Bản tóm tắt ở cấp tài liệu kèm outline (danh sách mục/heading) của một TaiLieu, được sinh khi nạp, phục vụ câu hỏi tổng quan và gợi ý.
- **QuyTacRanhGioi**: Quy tắc khai báo dấu hiệu bắt đầu một đoạn mới (từ khóa hoặc mẫu kèm điều kiện) do người dùng cấu hình qua UI, lưu dưới dạng dữ liệu (không phải mã nguồn) và áp dụng khi cắt lại Chunk.
- **CauHinhTruyXuat**: Tập cấu hình truy xuất theo từng KhongGianTaiLieu, gồm ngưỡng "không tìm thấy", ngưỡng "đủ liên quan", số Chunk k, và trọng số tìm kiếm vector so với BM25; có giá trị mặc định.
- **MauPrompt** (PromptTemplate): Bản mẫu hướng dẫn cho vai trò tổng hợp, xác minh hoặc chuẩn hóa, lưu dưới dạng dữ liệu, do TaiKhoan VaiTro QUAN_TRI chỉnh; có các ràng buộc an toàn bất biến không bị ghi đè.
- **Embedding_Provider**: Nhà cung cấp mô hình embedding, đăng ký qua registry song song với LLM_Provider.
- **KhoaApiNguoiDung** (per-user API key / BYOK): Khóa API riêng của một TaiKhoan dùng cho LLM_Provider hoặc Embedding_Provider, lưu ở dạng mã hóa, không ghi vào log và được che (masked) khi hiển thị.
- **RRF**: Reciprocal Rank Fusion — phương pháp hợp nhất xếp hạng kết quả từ nhiều nguồn tìm kiếm.
- **BM25**: Thuật toán xếp hạng tìm kiếm từ khóa dựa trên tần suất từ.
- **Web_App**: Client web hiện có (React 19 + Vite + Tailwind) chạy trên trình duyệt, tiêu thụ REST API của backend.
- **Mobile_App**: Ứng dụng di động native (React Native + Expo) chạy trên iOS và Android, là một client ngang hàng với Web_App (không phải webview/iframe của Web_App), tiêu thụ cùng REST API của backend mà không yêu cầu endpoint mới.
- **Hop_Dong_API_Dung_Chung**: Tập kiểu dữ liệu TypeScript và định nghĩa hàm gọi API (hợp đồng API client) dùng chung giữa Web_App và Mobile_App, phản chiếu các DTO của backend; gồm cả logic gắn token và xử lý lỗi 401.
- **Kho_Token_Thiet_Bi**: Kho lưu trữ an toàn của thiết bị di động dùng để lưu token (Keychain trên iOS, Keystore/EncryptedSharedPreferences trên Android, truy cập qua expo-secure-store), thay cho localStorage của trình duyệt.

## Requirements

### Requirement 1: Tự đăng ký tài khoản

**User Story:** Là một người dùng mới, tôi muốn tự đăng ký một tài khoản, để tôi có thể sử dụng nền tảng mà không cần quản trị viên tạo hộ.

#### Acceptance Criteria

1. WHEN một người dùng gửi yêu cầu đăng ký với email đúng định dạng và không quá 254 ký tự, tên đăng nhập dài 3–30 ký tự, và mật khẩu dài 8–64 ký tự, THE Auth_Service SHALL tạo một TaiKhoan mới với VaiTro mặc định là NGUOI_DUNG trong vòng 3 giây.
2. WHEN một TaiKhoan được tạo, THE Auth_Service SHALL lưu mật khẩu dưới dạng giá trị đã băm và SHALL không lưu mật khẩu ở dạng văn bản gốc.
3. IF email hoặc tên đăng nhập đã tồn tại trong He_Thong, THEN THE Auth_Service SHALL từ chối yêu cầu đăng ký, không tạo TaiKhoan mới, và trả về thông báo lỗi nêu rõ trường bị trùng.
4. IF mật khẩu có độ dài nhỏ hơn 8 ký tự hoặc lớn hơn 64 ký tự, THEN THE Auth_Service SHALL từ chối yêu cầu đăng ký, không tạo TaiKhoan mới, và trả về thông báo lỗi về yêu cầu độ dài mật khẩu.
5. IF email không đúng định dạng địa chỉ thư điện tử hoặc dài hơn 254 ký tự, THEN THE Auth_Service SHALL từ chối yêu cầu đăng ký, không tạo TaiKhoan mới, và trả về thông báo lỗi về định dạng email.
6. IF một trong các trường bắt buộc (email, tên đăng nhập, mật khẩu) bị thiếu hoặc rỗng, THEN THE Auth_Service SHALL từ chối yêu cầu đăng ký, không tạo TaiKhoan mới, và trả về thông báo lỗi nêu rõ trường còn thiếu.
7. IF tên đăng nhập có độ dài nhỏ hơn 3 ký tự hoặc lớn hơn 30 ký tự, THEN THE Auth_Service SHALL từ chối yêu cầu đăng ký, không tạo TaiKhoan mới, và trả về thông báo lỗi về ràng buộc độ dài tên đăng nhập.

### Requirement 2: Đăng nhập, đăng xuất và xác thực phiên

**User Story:** Là một người dùng đã đăng ký, tôi muốn đăng nhập và đăng xuất an toàn, để chỉ tôi truy cập được tài khoản và dữ liệu của mình.

#### Acceptance Criteria

1. WHEN một người dùng gửi tên đăng nhập và mật khẩu khớp với một TaiKhoan đang hoạt động, THE Auth_Service SHALL tạo một PhienXacThuc có thời hạn cấu hình được (mặc định 60 phút, xem Requirement 23) và trả về token kèm VaiTro của TaiKhoan trong vòng 2 giây.
2. IF tên đăng nhập không tồn tại hoặc mật khẩu không khớp, THEN THE Auth_Service SHALL từ chối đăng nhập, không tạo PhienXacThuc, và trả về thông báo lỗi xác thực chung không tiết lộ trường nào sai.
3. IF tên đăng nhập hoặc mật khẩu bị rỗng hoặc dài hơn 255 ký tự, THEN THE Auth_Service SHALL từ chối đăng nhập, không tạo PhienXacThuc, và trả về thông báo lỗi về đầu vào không hợp lệ.
4. IF một TaiKhoan có 5 lần đăng nhập thất bại liên tiếp trong vòng 15 phút, THEN THE Auth_Service SHALL tạm khóa đăng nhập của TaiKhoan đó trong 15 phút và từ chối các yêu cầu đăng nhập trong thời gian khóa.
5. WHEN một yêu cầu kèm token hợp lệ và còn hạn, THE Auth_Service SHALL xác định TaiKhoan tương ứng và cho phép xử lý yêu cầu.
6. IF một yêu cầu tới tài nguyên cần xác thực mà không kèm token hợp lệ, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 401.
7. WHEN token của một PhienXacThuc quá hạn, THE Auth_Service SHALL từ chối yêu cầu kèm token đó với trạng thái 401.
8. WHEN một người dùng yêu cầu đăng xuất, THE Auth_Service SHALL chấm dứt hiệu lực PhienXacThuc hiện tại của TaiKhoan đó trong vòng 2 giây.
9. WHEN một yêu cầu kèm token thuộc một PhienXacThuc đã bị chấm dứt do đăng xuất, THE Auth_Service SHALL từ chối yêu cầu với trạng thái 401.

### Requirement 3: Cô lập dữ liệu giữa các người dùng

**User Story:** Là một người dùng, tôi muốn dữ liệu của tôi tách biệt hoàn toàn với người dùng khác, để không ai khác xem hay sửa được tài liệu và lịch sử của tôi.

#### Acceptance Criteria

1. WHEN một TaiKhoan đã xác thực yêu cầu danh sách KhongGianTaiLieu, THE He_Thong SHALL chỉ trả về các KhongGianTaiLieu mà TaiKhoan đó sở hữu, cùng các KhongGianTaiLieu được chia sẻ cho TaiKhoan đó WHERE tính năng chia sẻ được bật, và SHALL không tiết lộ thông tin của KhongGianTaiLieu thuộc TaiKhoan khác.
2. IF một TaiKhoan yêu cầu đọc, sửa hoặc xóa một KhongGianTaiLieu mà TaiKhoan đó không sở hữu, và (khi tính năng chia sẻ được bật) cũng không được chia sẻ quyền tương ứng, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403, không thực hiện thao tác, và trả về thông báo lỗi về thiếu quyền.
3. IF một TaiKhoan yêu cầu đọc, sửa hoặc xóa một TaiLieu thuộc KhongGianTaiLieu mà TaiKhoan đó không có quyền, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403, không thực hiện thao tác, và trả về thông báo lỗi về thiếu quyền.
4. WHEN Query_Pipeline thực hiện truy xuất cho một truy vấn, THE Vector_Store SHALL loại bỏ các Chunk không thuộc KhongGianTaiLieu mà TaiKhoan thực hiện truy vấn có quyền truy cập trước khi trả về kết quả.
5. WHEN một TaiKhoan đã xác thực yêu cầu LichSuTroChuyen, THE He_Thong SHALL chỉ trả về LichSuTroChuyen thuộc về TaiKhoan đó và SHALL không tiết lộ LichSuTroChuyen của TaiKhoan khác.
6. IF một yêu cầu truy cập dữ liệu người dùng không kèm danh tính đã xác thực, THEN THE He_Thong SHALL từ chối yêu cầu và SHALL không trả về bất kỳ dữ liệu nào.
7. IF một TaiKhoan yêu cầu sửa hoặc xóa một mục LichSuTroChuyen không thuộc về mình, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403 và không thay đổi dữ liệu.
8. WHERE một KhongGianTaiLieu được chia sẻ cho nhiều TaiKhoan, THE He_Thong SHALL giữ LichSuTroChuyen riêng theo từng TaiKhoan, sao cho mỗi TaiKhoan chỉ thấy LichSuTroChuyen của chính mình trong KhongGianTaiLieu đó.

### Requirement 4: Quản lý không gian tài liệu

**User Story:** Là một người dùng, tôi muốn tạo và quản lý nhiều không gian tài liệu, để tổ chức tài liệu theo từng lĩnh vực hoặc dự án riêng.

#### Acceptance Criteria

1. WHEN một TaiKhoan gửi yêu cầu tạo KhongGianTaiLieu kèm tên dài 1–100 ký tự sau khi loại bỏ khoảng trắng đầu/cuối, THE He_Thong SHALL tạo một KhongGianTaiLieu mới với TaiKhoan đó là chủ sở hữu.
2. IF tên KhongGianTaiLieu rỗng sau khi loại bỏ khoảng trắng hoặc dài hơn 100 ký tự, THEN THE He_Thong SHALL từ chối yêu cầu tạo và trả về thông báo lỗi về ràng buộc độ dài tên.
3. WHEN chủ sở hữu một KhongGianTaiLieu yêu cầu đổi tên với tên dài 1–100 ký tự sau khi loại bỏ khoảng trắng, THE He_Thong SHALL cập nhật tên của KhongGianTaiLieu đó.
4. WHEN chủ sở hữu một KhongGianTaiLieu yêu cầu cập nhật mô tả với độ dài không quá 1000 ký tự, THE He_Thong SHALL cập nhật mô tả của KhongGianTaiLieu đó.
5. IF một TaiKhoan không phải chủ sở hữu yêu cầu đổi tên, cập nhật mô tả hoặc xóa một KhongGianTaiLieu, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403 và không thay đổi KhongGianTaiLieu.
6. WHEN chủ sở hữu một KhongGianTaiLieu yêu cầu xóa KhongGianTaiLieu đó, THE He_Thong SHALL xóa KhongGianTaiLieu cùng toàn bộ TaiLieu và Chunk thuộc về nó.
7. IF việc xóa một KhongGianTaiLieu thất bại giữa chừng, THEN THE He_Thong SHALL hoàn tác để giữ nguyên trạng thái KhongGianTaiLieu cùng TaiLieu và Chunk của nó.
8. WHEN một KhongGianTaiLieu bị xóa, THE He_Thong SHALL xóa các TrichDan tham chiếu tới KhongGianTaiLieu đó và đánh dấu các LichSuTroChuyen liên quan là không còn khả dụng.

### Requirement 5: Tải lên và quản lý tài liệu trong không gian

**User Story:** Là một người dùng, tôi muốn tải tài liệu lên một không gian của mình và quản lý chúng, để hệ thống có thể trả lời câu hỏi dựa trên nội dung đó.

#### Acceptance Criteria

1. WHEN một TaiKhoan tải lên một TaiLieu định dạng được hỗ trợ vào một KhongGianTaiLieu mà TaiKhoan đó có quyền ghi, THE Document_Pipeline SHALL phân tích và tách đoạn TaiLieu thành các Chunk, đặt TaiLieu sang trạng thái ĐÃ_PARSE_CHỜ_DUYỆT để xem trước, và SHALL trả về số Chunk đã tạo; việc tạo embedding và lưu trữ Chunk vào Vector_Store chỉ thực hiện sau khi TaiLieu được chốt (xem mục 13 và Requirement 18 mục 1).
2. IF một TaiKhoan tải lên TaiLieu vào một KhongGianTaiLieu mà TaiKhoan đó không có quyền ghi, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403, không lưu bất kỳ Chunk nào, và trả về thông báo lỗi về thiếu quyền.
3. IF kích thước tệp tải lên vượt quá giới hạn kích thước tệp cấu hình được (mặc định 50MB, xem Requirement 23), THEN THE Document_Pipeline SHALL từ chối tệp, không lưu bất kỳ Chunk nào, và trả về thông báo lỗi về giới hạn kích thước.
4. IF định dạng tệp tải lên không nằm trong tập định dạng được hỗ trợ, THEN THE Document_Pipeline SHALL từ chối tệp, không lưu bất kỳ Chunk nào, và trả về thông báo lỗi về định dạng không được hỗ trợ.
5. IF một TaiLieu tải lên tạo ra 0 Chunk sau khi phân tích, THEN THE Document_Pipeline SHALL từ chối TaiLieu, không lưu bất kỳ Chunk nào, và trả về thông báo lỗi về việc không trích xuất được nội dung văn bản.
6. IF việc lưu trữ Chunk của một TaiLieu thất bại giữa chừng, THEN THE Document_Pipeline SHALL hoàn tác các Chunk đã lưu của TaiLieu đó để Vector_Store không còn trạng thái nạp dở.
7. WHEN một TaiKhoan yêu cầu danh sách TaiLieu của một KhongGianTaiLieu mà TaiKhoan đó có quyền với tham số trang (page ≥ 1, pageSize 1–100, mặc định 20), THE He_Thong SHALL trả về danh sách TaiLieu theo trang kèm tổng số TaiLieu.
8. WHEN một TaiKhoan yêu cầu xóa một TaiLieu đang tồn tại mà TaiKhoan đó có quyền ghi, THE He_Thong SHALL xóa TaiLieu cùng toàn bộ Chunk thuộc về nó khỏi Vector_Store và xác nhận đã xóa.
9. IF một TaiKhoan yêu cầu xóa một TaiLieu không tồn tại, THEN THE He_Thong SHALL trả về thông báo lỗi không tìm thấy và không thay đổi Vector_Store.
10. WHEN một TaiLieu được nạp thành công, THE Document_Pipeline SHALL sinh một TomTatTaiLieu gồm tóm tắt nội dung và outline (danh sách mục/tiêu đề) và lưu kèm KhongGianTaiLieu.
11. WHEN một TaiKhoan tải lên TaiLieu, THE He_Thong SHALL cho phép chọn ChienLuocChunk cho KhongGianTaiLieu hoặc TaiLieu đó, với mặc định là chế độ "Tự động".
12. WHEN ChienLuocChunk hoặc tham số chunk của một TaiLieu thay đổi, THE Document_Pipeline SHALL cắt lại và embed lại TaiLieu đó, thay thế sạch các Chunk cũ (idempotent), không để lẫn Chunk cũ và mới.
13. THE He_Thong SHALL quản lý vòng đời của một TaiLieu qua trình tự trạng thái nạp → parse → ĐÃ_PARSE_CHỜ_DUYỆT → chốt → ĐÃ_EMBED, trong đó embedding chỉ được tạo và lưu vào Vector_Store sau khi TaiLieu chuyển sang ĐÃ_EMBED.
14. WHILE một TaiLieu ở trạng thái ĐÃ_PARSE_CHỜ_DUYỆT, THE He_Thong SHALL cho phép TaiKhoan có quyền ghi xem trước và điều chỉnh các Chunk trước khi chốt embed.

### Requirement 6: Truy vấn hỏi đáp theo không gian đã chọn

**User Story:** Là một người dùng, tôi muốn đặt câu hỏi và nhận câu trả lời dựa trên tài liệu trong không gian tôi chọn, để tra cứu thông tin của riêng mình.

#### Acceptance Criteria

1. WHEN một TaiKhoan gửi câu hỏi kèm định danh một KhongGianTaiLieu mà TaiKhoan đó có quyền, THE Query_Pipeline SHALL truy xuất Chunk và tổng hợp câu trả lời chỉ từ các Chunk thuộc KhongGianTaiLieu đó, không dùng Chunk của KhongGianTaiLieu khác.
2. IF một TaiKhoan gửi câu hỏi kèm định danh một KhongGianTaiLieu không tồn tại hoặc TaiKhoan đó không có quyền, THEN THE He_Thong SHALL từ chối truy vấn với trạng thái 404 hoặc 403 tương ứng và không thực hiện truy xuất.
3. IF câu hỏi rỗng sau khi loại bỏ khoảng trắng hoặc dài hơn 1000 ký tự, THEN THE He_Thong SHALL từ chối truy vấn và trả về thông báo lỗi về ràng buộc độ dài câu hỏi.
4. WHEN Query_Pipeline thực hiện truy xuất, THE Vector_Store SHALL hợp nhất kết quả tìm kiếm vector và tìm kiếm từ khóa BM25 bằng phương pháp RRF theo trọng số trong CauHinhTruyXuat của KhongGianTaiLieu và trả về tối đa k Chunk, với k lấy từ CauHinhTruyXuat (mặc định k = 8).
5. IF Diem_Lien_Quan cao nhất của các Chunk truy xuất được nhỏ hơn ngưỡng "không tìm thấy" trong CauHinhTruyXuat của KhongGianTaiLieu (mặc định 0.3), THEN THE Query_Pipeline SHALL trả về phản hồi "không tìm thấy" và SHALL không gọi LLM_Provider tổng hợp.
6. IF Diem_Lien_Quan cao nhất lớn hơn hoặc bằng ngưỡng "không tìm thấy" (mặc định 0.3) và nhỏ hơn ngưỡng "đủ liên quan" trong CauHinhTruyXuat của KhongGianTaiLieu (mặc định 0.5), THEN THE Query_Pipeline SHALL trả về phản hồi "chưa đủ liên quan" và SHALL không gọi LLM_Provider tổng hợp.
7. WHEN câu hỏi được gửi ở dạng không dấu, THE Query_Pipeline SHALL chuẩn hóa câu hỏi bằng cách thêm dấu trước khi truy xuất, và SHALL giữ nguyên câu gốc nếu kết quả chuẩn hóa làm thay đổi tập từ sau khi bỏ dấu.
8. WHEN Query_Pipeline xử lý một truy vấn thường, THE He_Thong SHALL trả về câu trả lời tổng hợp trong một trần thời gian end-to-end cấu hình được (mặc định 30 giây), và bước xác minh chéo SHALL chạy không đồng bộ, không tính vào độ trễ trả lời câu trả lời tổng hợp (nhất quán với Requirement 8).

### Requirement 7: Bám sát ngữ cảnh và trích dẫn nguồn

**User Story:** Là một người dùng, tôi muốn câu trả lời chỉ dựa trên tài liệu của tôi và có trích dẫn nguồn, để tôi tin tưởng và kiểm chứng được thông tin.

#### Acceptance Criteria

1. WHEN Query_Pipeline tổng hợp câu trả lời, THE LLM_Provider SHALL tạo nội dung chỉ dựa trên các Chunk được cung cấp.
2. WHEN Query_Pipeline tổng hợp câu trả lời, THE LLM_Provider SHALL không thêm bất kỳ thông tin nào ngoài các Chunk được cung cấp.
3. IF các Chunk được cung cấp không đủ thông tin để trả lời câu hỏi, THEN THE LLM_Provider SHALL nêu rõ không tìm thấy căn cứ trong tài liệu và SHALL không suy đoán nội dung ngoài các Chunk.
4. WHEN Query_Pipeline tổng hợp câu trả lời từ N Chunk, THE LLM_Provider SHALL chèn marker trích dẫn nội tuyến `[n]` với n trong khoảng 1..N, mỗi marker tương ứng đúng một Chunk nguồn được dùng.
5. WHEN một câu trả lời được trả về, THE He_Thong SHALL kèm danh sách TrichDan sao cho mỗi marker `[n]` ánh xạ đúng một mục TrichDan cho phép người dùng xem nội dung Chunk nguồn tương ứng.
6. THE LLM_Provider SHALL dùng một tập hướng dẫn (prompt) tổng hợp duy nhất không chứa quy tắc hay thuật ngữ riêng của bất kỳ lĩnh vực nào và áp dụng đồng nhất cho mọi truy vấn.
7. THE He_Thong SHALL coi các ràng buộc bám sát ngữ cảnh ở mục 1, 2 và 3 là thuộc tính chất lượng được kiểm chứng qua cơ chế xác minh chéo ở Requirement 8 và qua lấy mẫu (sampling) định kỳ, thay vì kiểm chứng tuyệt đối trên mọi câu trả lời.

### Requirement 8: Xác minh chéo và dự phòng

**User Story:** Là một người dùng, tôi muốn biết mức độ tin cậy của câu trả lời và vẫn nhận được thông tin khi mô hình gặp sự cố, để không bị mất kết quả.

#### Acceptance Criteria

1. WHEN một câu trả lời đã được tổng hợp xong, THE Query_Pipeline SHALL gửi câu trả lời đó cùng các Chunk nguồn tới LLM_Provider xác minh và SHALL gắn vào phản hồi đúng một nhãn trạng thái thuộc tập { "đã xác minh", "có mâu thuẫn", "chưa xác minh" } trong vòng llm_timeout cấu hình được (mặc định 30 giây, xem Requirement 23) kể từ khi tổng hợp xong.
2. IF LLM_Provider xác minh không khả dụng hoặc đã hết hạn mức sử dụng, THEN THE Query_Pipeline SHALL gắn nhãn "chưa xác minh" và SHALL trả về câu trả lời đã tổng hợp mà không trả về lỗi.
3. IF LLM_Provider xác minh không phản hồi trong vòng llm_timeout cấu hình được (mặc định 30 giây, xem Requirement 23), THEN THE Query_Pipeline SHALL gắn nhãn "chưa xác minh" và SHALL trả về câu trả lời đã tổng hợp mà không trả về lỗi.
4. IF LLM_Provider tổng hợp trả về lỗi hoặc không phản hồi trong vòng llm_timeout cấu hình được (mặc định 30 giây, xem Requirement 23), THEN THE Query_Pipeline SHALL trả về các Chunk gốc đã truy xuất làm phản hồi dự phòng kèm chỉ báo cho người dùng rằng đây là phản hồi dự phòng và SHALL không trả về lỗi.

### Requirement 9: Lịch sử trò chuyện theo người dùng

**User Story:** Là một người dùng, tôi muốn xem lại lịch sử hỏi đáp của mình theo từng không gian, để theo dõi và tiếp tục công việc trước đó.

#### Acceptance Criteria

1. WHEN một câu trả lời được trả về cho một TaiKhoan, THE He_Thong SHALL lưu cặp câu hỏi - câu trả lời vào LichSuTroChuyen gắn với TaiKhoan và KhongGianTaiLieu tương ứng, kèm mốc thời gian tạo (timestamp) để phục vụ sắp xếp.
2. IF việc lưu cặp câu hỏi - câu trả lời vào LichSuTroChuyen thất bại, THEN THE He_Thong SHALL vẫn trả câu trả lời cho TaiKhoan và phản hồi thông báo lỗi cho biết lịch sử chưa được lưu, đồng thời không tạo mục LichSuTroChuyen không hoàn chỉnh.
3. WHEN một TaiKhoan yêu cầu LichSuTroChuyen của một KhongGianTaiLieu mà TaiKhoan đó có quyền, THE He_Thong SHALL trả về các cặp câu hỏi - câu trả lời thuộc TaiKhoan đó theo thứ tự thời gian tạo giảm dần (mới nhất trước), tối đa 50 mục mỗi lần trả về.
4. IF một TaiKhoan yêu cầu LichSuTroChuyen của một KhongGianTaiLieu mà TaiKhoan đó không có quyền, THEN THE He_Thong SHALL từ chối yêu cầu và phản hồi thông báo lỗi cho biết không có quyền truy cập, đồng thời không trả về bất kỳ cặp câu hỏi - câu trả lời nào.
5. WHEN một TaiKhoan yêu cầu LichSuTroChuyen của một KhongGianTaiLieu mà TaiKhoan đó có quyền nhưng chưa có mục nào, THE He_Thong SHALL trả về danh sách rỗng.
6. WHEN một TaiKhoan yêu cầu xóa một mục đang tồn tại trong LichSuTroChuyen của chính mình, THE He_Thong SHALL xóa mục đó khỏi LichSuTroChuyen của TaiKhoan đó và giữ nguyên các mục còn lại.
7. IF một TaiKhoan yêu cầu xóa một mục không tồn tại hoặc không thuộc LichSuTroChuyen của chính mình, THEN THE He_Thong SHALL từ chối yêu cầu và phản hồi thông báo lỗi cho biết mục không hợp lệ, đồng thời không thay đổi bất kỳ mục nào trong LichSuTroChuyen.
8. WHEN một TaiLieu được cắt lại (re-index), THE He_Thong SHALL đánh dấu các TrichDan trong LichSuTroChuyen trỏ tới Chunk cũ của TaiLieu đó là "nguồn đã thay đổi/không còn khả dụng" thay vì để chúng trỏ tới Chunk sai.

### Requirement 10: Vai trò và quyền quản trị

**User Story:** Là một quản trị viên, tôi muốn quản lý người dùng và giám sát hệ thống, để duy trì vận hành và xử lý vi phạm.

#### Acceptance Criteria

1. WHERE một TaiKhoan có VaiTro QUAN_TRI, WHEN TaiKhoan đó yêu cầu danh sách TaiKhoan, THE He_Thong SHALL trả về danh sách toàn bộ TaiKhoan kèm định danh, VaiTro và trạng thái kích hoạt của từng TaiKhoan.
2. WHERE một TaiKhoan có VaiTro QUAN_TRI, WHEN TaiKhoan đó yêu cầu vô hiệu hóa một TaiKhoan khác đang hoạt động, THE He_Thong SHALL đặt TaiKhoan đó sang trạng thái bị vô hiệu hóa.
3. WHERE một TaiKhoan có VaiTro QUAN_TRI, WHEN TaiKhoan đó yêu cầu kích hoạt lại một TaiKhoan đang bị vô hiệu hóa, THE He_Thong SHALL đặt TaiKhoan đó về trạng thái hoạt động.
4. IF một TaiKhoan có VaiTro QUAN_TRI yêu cầu vô hiệu hóa chính mình, THEN THE He_Thong SHALL từ chối yêu cầu và giữ nguyên trạng thái hoạt động của TaiKhoan đó.
5. IF một thao tác quản trị nhắm tới một TaiKhoan không tồn tại, THEN THE He_Thong SHALL từ chối yêu cầu và trả về thông báo lỗi không tìm thấy TaiKhoan.
6. IF một TaiKhoan có VaiTro NGUOI_DUNG yêu cầu một thao tác quản trị, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403 và không thực hiện thao tác.
7. IF một TaiKhoan bị vô hiệu hóa cố gắng đăng nhập, THEN THE Auth_Service SHALL từ chối đăng nhập, không cấp token phiên, và trả về thông báo lỗi về trạng thái tài khoản bị vô hiệu hóa.
8. WHEN một TaiKhoan bị vô hiệu hóa, THE He_Thong SHALL từ chối mọi yêu cầu kèm token của TaiKhoan đó với trạng thái 401 kể từ thời điểm bị vô hiệu hóa, thu hồi các PhienXacThuc đang hoạt động của TaiKhoan đó mà không chờ token hết hạn.

### Requirement 11: Chia sẻ không gian tài liệu (tùy chọn)

**User Story:** Là một người dùng, tôi muốn chia sẻ một không gian tài liệu cho người dùng khác, để cộng tác hỏi đáp trên cùng tập tài liệu.

#### Acceptance Criteria

1. WHERE tính năng chia sẻ được bật, WHEN chủ sở hữu một KhongGianTaiLieu cấp quyền truy cập cho một TaiKhoan khác với mức quyền thuộc tập { CHI_DOC, GHI }, THE He_Thong SHALL ghi nhận hoặc cập nhật quyền chia sẻ trong vòng 5 giây và trả về phản hồi xác nhận.
2. WHERE một TaiKhoan được chia sẻ KhongGianTaiLieu với mức quyền CHI_DOC, THE He_Thong SHALL cho phép TaiKhoan đó truy vấn và xem TaiLieu nhưng SHALL từ chối thao tác tải lên hoặc xóa TaiLieu với trạng thái 403.
3. WHERE một TaiKhoan được chia sẻ KhongGianTaiLieu với mức quyền GHI, THE He_Thong SHALL cho phép TaiKhoan đó truy vấn, xem, tải lên và xóa TaiLieu trong KhongGianTaiLieu đó.
4. IF chủ sở hữu cấp quyền chia sẻ với mức quyền không thuộc tập { CHI_DOC, GHI }, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 400 và giữ nguyên các quyền chia sẻ hiện có.
5. IF chủ sở hữu cấp quyền chia sẻ cho một TaiKhoan không tồn tại, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 404 và giữ nguyên các quyền chia sẻ hiện có.
6. WHEN chủ sở hữu một KhongGianTaiLieu thu hồi quyền chia sẻ của một TaiKhoan, THE He_Thong SHALL chấm dứt quyền truy cập của TaiKhoan đó trong vòng 5 giây, và mọi yêu cầu sau đó của TaiKhoan đó tới KhongGianTaiLieu tương ứng SHALL bị từ chối với trạng thái 403.
7. IF một TaiKhoan không phải chủ sở hữu cố gắng chia sẻ hoặc thu hồi quyền của một KhongGianTaiLieu, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403.

### Requirement 12: Hạn mức và giới hạn tài nguyên

**User Story:** Là một quản trị viên, tôi muốn áp hạn mức tài nguyên cho mỗi người dùng, để bảo vệ tài nguyên hệ thống khỏi lạm dụng.

#### Acceptance Criteria

1. IF một TaiKhoan yêu cầu tạo thêm KhongGianTaiLieu khiến số KhongGianTaiLieu vượt quá HanMuc số KhongGianTaiLieu (mặc định 50, cấu hình trong khoảng 1–1.000), THEN THE He_Thong SHALL từ chối yêu cầu, không tạo KhongGianTaiLieu, và trả về thông báo lỗi về vượt hạn mức.
2. IF một TaiKhoan tải lên một TaiLieu khiến tổng dung lượng vượt quá HanMuc dung lượng của TaiKhoan đó (mặc định 5 GB, cấu hình trong khoảng 1 MB–1.024 GB), THEN THE He_Thong SHALL từ chối tải lên, không lưu bất kỳ Chunk nào, và trả về thông báo lỗi về vượt hạn mức dung lượng.
3. IF một TaiKhoan tải lên một TaiLieu khiến số TaiLieu trong một KhongGianTaiLieu vượt quá HanMuc số TaiLieu (mặc định 1.000, cấu hình trong khoảng 1–100.000), THEN THE He_Thong SHALL từ chối tải lên, không lưu bất kỳ Chunk nào, và trả về thông báo lỗi về vượt hạn mức số tài liệu.
4. WHEN một yêu cầu sử dụng tài nguyên khiến mức sử dụng đạt đúng bằng HanMuc tương ứng mà không vượt quá, THE He_Thong SHALL cho phép thực hiện yêu cầu đó.
5. WHERE một TaiKhoan có VaiTro QUAN_TRI, WHEN TaiKhoan đó cấu hình HanMuc cho một TaiKhoan với giá trị nằm trong khoảng hợp lệ, THE He_Thong SHALL cập nhật HanMuc tương ứng.
6. IF một quản trị viên cấu hình HanMuc với giá trị nằm ngoài khoảng hợp lệ, THEN THE He_Thong SHALL từ chối yêu cầu và giữ nguyên HanMuc hiện có.
7. WHEN hai hay nhiều thao tác đồng thời cùng tiêu thụ tài nguyên của một TaiKhoan, THE He_Thong SHALL thực hiện việc kiểm tra và áp HanMuc một cách nguyên tử sao cho tổng mức sử dụng không vượt quá HanMuc tương ứng.

### Requirement 13: Cấu hình nhà cung cấp mô hình ngôn ngữ

**User Story:** Là một quản trị viên kỹ thuật, tôi muốn đổi nhà cung cấp mô hình cho từng vai trò mà không sửa mã lõi, để linh hoạt theo hạn mức và chất lượng.

#### Acceptance Criteria

1. WHERE một LLM_Provider đã được đăng ký trong registry, WHEN quản trị viên chỉ định LLM_Provider đó cho vai trò tổng hợp, xác minh hoặc chuẩn hóa qua cấu hình môi trường, THE He_Thong SHALL dùng đúng LLM_Provider đó cho vai trò tương ứng ở lần khởi tạo kế tiếp mà không cần sửa mã lõi tra cứu provider.
2. WHEN He_Thong khởi tạo, THE He_Thong SHALL tự động phát hiện mọi tệp provider có đăng ký (decorator) và nạp chúng vào registry mà không cần sửa thành phần tra cứu provider.
3. IF cấu hình chỉ định một LLM_Provider không tồn tại trong registry, THEN THE He_Thong SHALL dừng quá trình khởi tạo, không khởi động dịch vụ, và phát ra lỗi khởi tạo nêu rõ tên provider không hợp lệ.
4. IF cấu hình cho vai trò chuẩn hóa để trống, THEN THE He_Thong SHALL dùng LLM_Provider đã cấu hình cho vai trò xác minh để thực hiện vai trò chuẩn hóa.
5. IF cấu hình cho một vai trò bắt buộc (tổng hợp hoặc xác minh) để trống hoặc thiếu, THEN THE He_Thong SHALL dừng quá trình khởi tạo và phát ra lỗi khởi tạo nêu rõ vai trò chưa được cấu hình.

### Requirement 14: Ghi log tập trung

**User Story:** Là một người vận hành, tôi muốn hệ thống ghi log tập trung và đầy đủ ngữ cảnh, để giám sát và truy vết sự cố.

#### Acceptance Criteria

1. THE He_Thong SHALL ghi toàn bộ log qua một cấu hình logging tập trung với một nguồn cấu hình duy nhất (định dạng output, log level, đích ghi gồm console và file) dùng chung cho mọi thành phần.
2. WHEN He_Thong nhận một yêu cầu HTTP, THE He_Thong SHALL ghi một log entry mức INFO gồm phương thức, đường dẫn và định danh truy vết của yêu cầu.
3. IF một thao tác phát sinh lỗi, THEN THE He_Thong SHALL ghi một log entry mức ERROR gồm tên thao tác, các định danh liên quan (ví dụ requestId, mã đối tượng nghiệp vụ), thông điệp lỗi và dấu vết ngăn xếp (stack trace), đồng thời KHÔNG nuốt lỗi im lặng.
4. WHEN He_Thong ghi bất kỳ log entry nào, THE He_Thong SHALL loại trừ hoặc che (mask) các trường nhạy cảm gồm mật khẩu, token, khóa API và thông tin cá nhân đầy đủ khỏi nội dung log.
5. THE He_Thong SHALL bao gồm trong mỗi log entry tối thiểu các trường: dấu thời gian (timestamp), log level, tên module/nguồn phát sinh và message mô tả sự việc.
6. IF một yêu cầu HTTP không kèm định danh truy vết, THEN THE He_Thong SHALL tạo một định danh truy vết mới và gắn định danh đó vào mọi log entry phát sinh trong phạm vi yêu cầu đó.
7. WHERE He_Thong chạy ở môi trường production, THE He_Thong SHALL đặt log level mặc định là INFO; WHERE He_Thong chạy ở môi trường phát triển, THE He_Thong SHALL đặt log level mặc định là DEBUG.

### Requirement 15: Tổng quát hóa khỏi lĩnh vực luật

**User Story:** Là một người dùng ở bất kỳ lĩnh vực nào, tôi muốn nền tảng không giả định nội dung là văn bản luật, để dùng cho tài liệu thuộc mọi lĩnh vực.

#### Acceptance Criteria

1. THE Document_Pipeline SHALL áp dụng cùng một phương pháp tách đoạn cho TaiLieu thuộc mọi lĩnh vực, không yêu cầu TaiLieu phải tuân theo quy ước trình bày riêng của văn bản luật.
2. WHEN một TaiLieu không chứa dấu hiệu cấu trúc đặc thù của bất kỳ lĩnh vực nào, THE Document_Pipeline SHALL vẫn tách đoạn TaiLieu đó thành ít nhất một đoạn và hoàn tất việc nạp mà không phát sinh lỗi.
3. THE Query_Pipeline SHALL xử lý mọi truy vấn chỉ dựa trên TaiLieu đã được nạp vào KhongGianTaiLieu, không tham chiếu tới bất kỳ danh mục văn bản (manifest) cố định gắn với lĩnh vực luật nào.
4. WHEN He_Thong hiển thị gợi ý hoặc văn bản hướng dẫn cho một KhongGianTaiLieu, THE He_Thong SHALL sinh nội dung chỉ từ các TaiLieu thực có trong KhongGianTaiLieu đó tại thời điểm hiển thị, không dùng danh mục cố định theo lĩnh vực.
5. IF KhongGianTaiLieu không chứa TaiLieu nào khi He_Thong cần hiển thị gợi ý hoặc văn bản hướng dẫn, THEN THE He_Thong SHALL hiển thị thông báo cho biết chưa có tài liệu thay vì gợi ý hoặc nội dung mặc định theo lĩnh vực.

### Requirement 16: Truy vấn tổng quan và tóm tắt tài liệu

**User Story:** Là một người dùng, tôi muốn đặt câu hỏi mang tính tổng quát ("tài liệu gồm những mục nào", "nội dung chung là gì", "tóm tắt giúp tôi") và nhận được câu trả lời, để nắm bắt toàn cảnh tài liệu, vì truy xuất RAG theo top-K Chunk nhỏ không trả lời được câu hỏi ở phạm vi toàn cục.

#### Acceptance Criteria

1. WHEN một TaiKhoan gửi câu hỏi mang ý định tổng quan (không tham chiếu tới một Chunk hoặc mục cụ thể mà yêu cầu nội dung tổng thể, tóm tắt hoặc liệt kê mục) cho một KhongGianTaiLieu mà TaiKhoan đó có quyền, THE Query_Pipeline SHALL tạo câu trả lời dựa trên TomTatTaiLieu và outline của tất cả TaiLieu mà TaiKhoan đó có quyền đọc trong KhongGianTaiLieu đó thay vì chỉ dựa trên top-K Chunk.
2. WHEN Query_Pipeline trả lời một câu hỏi tổng quan dựa trên TomTatTaiLieu và outline, THE He_Thong SHALL kèm ít nhất một TrichDan, mỗi TrichDan trỏ đúng tới TaiLieu hoặc mục nguồn đã được dùng để tạo câu trả lời.
3. WHEN Query_Pipeline xử lý một câu hỏi tổng quan, THE He_Thong SHALL trả về câu trả lời trong vòng 30 giây.
4. IF LLM_Provider gặp lỗi hoặc không phản hồi trong vòng 30 giây khi xử lý câu hỏi tổng quan, THEN THE Query_Pipeline SHALL trả về TomTatTaiLieu gốc kèm thông báo đây là phản hồi dự phòng và SHALL không trả về lỗi.
5. IF một KhongGianTaiLieu mà TaiKhoan có quyền chưa chứa TaiLieu nào khi nhận một câu hỏi tổng quan hoặc tóm tắt, THEN THE He_Thong SHALL trả về thông báo cho biết chưa có tài liệu, theo cùng cách xử lý mô tả ở Requirement 15 mục 5, và SHALL không gọi LLM_Provider tổng hợp.
6. WHEN một TaiKhoan gửi câu hỏi mà không ép chế độ trả lời, THE He_Thong SHALL phân loại ý định truy vấn thành tổng quan hoặc chi tiết bằng một cơ chế xác định (cùng đầu vào cho cùng kết quả), dựa trên một tập mẫu/từ khóa cấu hình được (ví dụ "tóm tắt", "gồm những mục nào", "nội dung chung") hoặc một bộ phân loại có ngưỡng cấu hình được.
7. WHEN một TaiKhoan ép chế độ trả lời (tổng quan hoặc chi tiết) cho một truy vấn, THE He_Thong SHALL trả lời theo chế độ được TaiKhoan chọn và bỏ qua kết quả phân loại ý định tự động.
8. IF kết quả phân loại ý định tự động không khớp ý định của TaiKhoan, THEN THE He_Thong SHALL cho phép TaiKhoan chuyển chế độ trả lời và gửi lại truy vấn để nhận câu trả lời theo chế độ được chọn.

### Requirement 17: Chiến lược chunk khả mở rộng qua registry

**User Story:** Là một quản trị viên kỹ thuật, tôi muốn thêm hoặc đổi thuật toán chunk mà không sửa mã lõi tách đoạn, để linh hoạt theo loại tài liệu, song song với cơ chế registry của LLM_Provider ở Requirement 13.

#### Acceptance Criteria

1. WHERE một ChienLuocChunk đã được đăng ký trong registry, WHEN một TaiKhoan chọn ChienLuocChunk đó cho một KhongGianTaiLieu hoặc TaiLieu qua cấu hình, THE He_Thong SHALL dùng đúng ChienLuocChunk đó khi cắt đoạn.
2. WHEN He_Thong khởi tạo, THE He_Thong SHALL tự động phát hiện mọi tệp chiến lược có đăng ký và nạp chúng vào registry mà không cần sửa thành phần tra cứu chiến lược hoặc Document_Pipeline lõi.
3. WHEN ở chế độ "Tự động" và một TomTatTaiLieu hoặc TaiLieu chứa dấu hiệu "Điều" theo sau bởi một hoặc nhiều chữ số ở đầu dòng, THE He_Thong SHALL chọn chiến lược tách theo luật, bất kể có xuất hiện đồng thời dấu hiệu nào khác (heading markdown, phân trang PDF).
4. WHEN ở chế độ "Tự động", một TaiLieu KHÔNG chứa dấu hiệu "Điều" theo sau bởi chữ số ở đầu dòng, VÀ chứa ít nhất một heading markdown, THE He_Thong SHALL chọn chiến lược structure-aware.
5. WHEN ở chế độ "Tự động", một TaiLieu KHÔNG chứa dấu hiệu "Điều" theo sau bởi chữ số ở đầu dòng, KHÔNG chứa heading markdown, VÀ là PDF có phân trang, THE He_Thong SHALL chọn chiến lược page.
6. WHEN ở chế độ "Tự động" và một TaiLieu KHÔNG khớp bất kỳ dấu hiệu ưu tiên cao hơn nào (luật, structure-aware, page), THE He_Thong SHALL chọn chiến lược recursive.
7. IF cấu hình chỉ định một ChienLuocChunk không tồn tại trong registry, THEN THE He_Thong SHALL từ chối thao tác cắt đoạn, trả về thông báo lỗi nêu rõ tên ChienLuocChunk không hợp lệ, và giữ nguyên trạng thái TaiLieu mà không ghi bất kỳ đoạn cắt nào.
8. WHEN ở chế độ "Tự động" và nhiều dấu hiệu cùng xuất hiện trong một TaiLieu, THE He_Thong SHALL đánh giá các dấu hiệu theo thứ tự ưu tiên cố định (1) luật, (2) structure-aware, (3) page, (4) recursive, và chọn chiến lược đầu tiên khớp.

### Requirement 18: Sửa và tinh chỉnh chunk tự phục vụ trong ứng dụng

**User Story:** Là một người dùng, khi chế độ "Tự động" chọn chiến lược cho ra Chunk không hợp lý (ví dụ một Điều luật bị tách sai do định dạng bất thường), tôi muốn tự sửa ngay trên ứng dụng mà không phải nhờ lập trình viên sửa mã rồi triển khai lại.

#### Acceptance Criteria

1. THE He_Thong SHALL hiển thị bản xem trước (preview) các Chunk của một TaiLieu trước khi chốt embed, gồm danh sách Chunk theo thứ tự, ranh giới bắt đầu và kết thúc của từng Chunk, nội dung từng Chunk và tổng số Chunk, để người dùng kiểm tra ranh giới Chunk.
2. THE He_Thong SHALL cho phép một TaiKhoan đổi ChienLuocChunk hoặc tham số chunk của TaiLieu thuộc quyền ghi của mình rồi cắt lại, mà không yêu cầu thay đổi mã nguồn.
3. THE He_Thong SHALL cung cấp một giao diện riêng, tách biệt với luồng hỏi đáp, để sửa tay Chunk, gồm gộp hai Chunk liền nhau, tách một Chunk tại vị trí được chọn, và điều chỉnh ranh giới Chunk.
4. THE He_Thong SHALL cho phép một TaiKhoan khai báo QuyTacRanhGioi qua UI, lưu dưới dạng dữ liệu, và áp dụng QuyTacRanhGioi đó khi cắt lại.
5. WHERE một TaiKhoan có VaiTro NGUOI_DUNG, THE He_Thong SHALL cho phép TaiKhoan đó chỉnh ChienLuocChunk, tham số chunk, QuyTacRanhGioi và sửa tay Chunk cho KhongGianTaiLieu hoặc TaiLieu mà TaiKhoan đó có quyền ghi.
6. THE He_Thong SHALL luôn cung cấp thao tác "đặt lại về mặc định" để hoàn nguyên mọi tùy chỉnh chunk gồm ChienLuocChunk, tham số chunk, QuyTacRanhGioi và các chỉnh sửa tay của một TaiLieu hoặc KhongGianTaiLieu về cấu hình mặc định, kèm cắt lại.
7. WHEN một tùy chỉnh chunk được áp dụng hoặc đặt lại, THE Document_Pipeline SHALL cắt lại và embed lại, thay thế sạch các Chunk cũ (idempotent), và THE He_Thong SHALL hiển thị trạng thái đang xử lý lại.
8. WHEN một TaiKhoan thực hiện các thao tác sửa hoặc tinh chỉnh chunk gồm đổi ChienLuocChunk, đổi tham số, khai báo QuyTacRanhGioi và sửa tay Chunk, THE He_Thong SHALL hoàn tất các thao tác đó mà không yêu cầu thay đổi mã nguồn.
9. WHERE cần một thuật toán chunk hoàn toàn mới chưa có trong registry, THE He_Thong SHALL yêu cầu lập trình viên bổ sung một tệp chiến lược và đăng ký vào registry; mọi thao tác sửa hoặc tinh chỉnh chunk khác SHALL không yêu cầu thay đổi mã nguồn.
10. IF việc cắt lại hoặc embed lại thất bại khi áp dụng một tùy chỉnh chunk, THEN THE Document_Pipeline SHALL giữ nguyên các Chunk cũ, báo lỗi, và cho phép thử lại hoặc đặt lại về mặc định.
11. IF một thao tác sửa tay Chunk tạo ra Chunk không hợp lệ (ví dụ Chunk rỗng), THEN THE He_Thong SHALL từ chối thao tác, giữ nguyên các Chunk hiện có, và báo lý do.
### Requirement 19: Cấu hình truy xuất theo không gian (trong ứng dụng, không sửa mã)

**User Story:** Là một người dùng, tôi muốn tự điều chỉnh ngưỡng liên quan, số Chunk k và trọng số tìm kiếm vector/BM25 cho một không gian khi kết quả truy xuất không phù hợp với corpus của mình, để cải thiện chất lượng truy xuất mà không cần sửa mã.

#### Acceptance Criteria

1. WHERE một TaiKhoan có quyền ghi một KhongGianTaiLieu, WHEN TaiKhoan đó chỉnh CauHinhTruyXuat qua UI với ngưỡng "không tìm thấy" và ngưỡng "đủ liên quan" trong khoảng [0, 1], ngưỡng dưới nhỏ hơn hoặc bằng ngưỡng trên, k trong khoảng hợp lệ (mặc định 8) và trọng số vector/BM25 hợp lệ, THE He_Thong SHALL cập nhật CauHinhTruyXuat của KhongGianTaiLieu đó.
2. WHEN CauHinhTruyXuat của một KhongGianTaiLieu được cập nhật, THE He_Thong SHALL áp dụng cấu hình mới cho truy vấn kế tiếp tới KhongGianTaiLieu đó mà không cần sửa mã.
3. THE He_Thong SHALL cung cấp thao tác "đặt lại về mặc định" để hoàn nguyên CauHinhTruyXuat của một KhongGianTaiLieu về giá trị mặc định.
4. IF một TaiKhoan chỉnh CauHinhTruyXuat với giá trị nằm ngoài khoảng hợp lệ hoặc ngưỡng dưới lớn hơn ngưỡng trên, THEN THE He_Thong SHALL từ chối thay đổi và giữ nguyên CauHinhTruyXuat hiện có.
5. IF một TaiKhoan không có quyền ghi một KhongGianTaiLieu yêu cầu chỉnh CauHinhTruyXuat của KhongGianTaiLieu đó, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403 và giữ nguyên CauHinhTruyXuat hiện có.

### Requirement 20: Mẫu prompt chỉnh được bởi quản trị (trong ứng dụng)

**User Story:** Là một quản trị viên, tôi muốn chỉnh phong cách và định dạng câu trả lời khi prompt mặc định cho kết quả chưa phù hợp, để điều chỉnh đầu ra mà không cần sửa mã.

#### Acceptance Criteria

1. WHERE một TaiKhoan có VaiTro QUAN_TRI, WHEN TaiKhoan đó chỉnh MauPrompt cho vai trò tổng hợp, xác minh hoặc chuẩn hóa, THE He_Thong SHALL lưu MauPrompt dưới dạng dữ liệu và áp dụng MauPrompt đó cho truy vấn kế tiếp mà không cần sửa mã.
2. WHERE một TaiKhoan có VaiTro QUAN_TRI, THE He_Thong SHALL cung cấp thao tác "đặt lại về mặc định" để hoàn nguyên MauPrompt của một vai trò về nội dung mặc định.
3. THE He_Thong SHALL giữ các ràng buộc an toàn bất biến (chỉ dùng các Chunk được cung cấp, không thêm thông tin ngoài các Chunk, vẫn chèn marker trích dẫn `[n]`) và SHALL không cho MauPrompt ghi đè các ràng buộc này.
4. IF một TaiKhoan có VaiTro NGUOI_DUNG yêu cầu chỉnh MauPrompt, THEN THE He_Thong SHALL từ chối yêu cầu với trạng thái 403 và không thay đổi MauPrompt.

### Requirement 21: Cấu hình nhà cung cấp embedding qua registry

**User Story:** Là một quản trị viên kỹ thuật, tôi muốn đổi nhà cung cấp embedding cho từng không gian mà không sửa mã lõi, để linh hoạt theo chất lượng và hạn mức, song song với cơ chế registry của LLM_Provider ở Requirement 13.

#### Acceptance Criteria

1. WHERE một Embedding_Provider đã được đăng ký trong registry, WHEN quản trị viên chỉ định Embedding_Provider đó qua cấu hình, THE He_Thong SHALL dùng đúng Embedding_Provider đó ở lần khởi tạo kế tiếp mà không cần sửa mã lõi tra cứu provider.
2. WHEN He_Thong khởi tạo, THE He_Thong SHALL tự động phát hiện mọi tệp Embedding_Provider có đăng ký và nạp chúng vào registry mà không cần sửa thành phần tra cứu provider.
3. IF cấu hình chỉ định một Embedding_Provider không tồn tại trong registry, THEN THE He_Thong SHALL dừng quá trình khởi tạo, không khởi động dịch vụ, và phát ra lỗi khởi tạo nêu rõ tên provider không hợp lệ.
4. WHEN Embedding_Provider của một KhongGianTaiLieu thay đổi, THE He_Thong SHALL embed lại toàn bộ Chunk liên quan (idempotent, thay sạch vector cũ) và hiển thị trạng thái đang xử lý lại.

### Requirement 22: Khóa API riêng của người dùng (BYOK) và bảo mật khóa

**User Story:** Là một người dùng, tôi muốn dùng API key riêng của mình cho LLM hoặc embedding để tự chịu hạn mức, và yên tâm rằng khóa của tôi được bảo mật.

#### Acceptance Criteria

1. THE He_Thong SHALL cho phép một TaiKhoan nhập, cập nhật và xóa KhoaApiNguoiDung của mình cho từng LLM_Provider hoặc Embedding_Provider.
2. THE He_Thong SHALL lưu KhoaApiNguoiDung ở dạng mã hóa khi lưu trữ (encrypted at rest) và SHALL không lưu khóa ở dạng văn bản gốc.
3. WHEN He_Thong ghi log, THE He_Thong SHALL không ghi KhoaApiNguoiDung vào log (nhất quán với Requirement 14 mục 4) và SHALL không trả khóa ở dạng văn bản gốc qua API; khi hiển thị, THE He_Thong SHALL chỉ hiển thị khóa ở dạng che (masked).
4. WHEN một TaiKhoan đã cấu hình KhoaApiNguoiDung cho một vai trò, THE He_Thong SHALL dùng khóa đó cho các yêu cầu LLM hoặc embedding của chính TaiKhoan đó.
5. THE He_Thong SHALL cô lập KhoaApiNguoiDung giữa các TaiKhoan, sao cho khóa của một TaiKhoan SHALL không được dùng cho TaiKhoan khác.
6. IF KhoaApiNguoiDung thiếu khi một vai trò bắt buộc cần khóa, hoặc khóa không hợp lệ hoặc bị nhà cung cấp từ chối, THEN THE He_Thong SHALL trả về thông báo lỗi rõ ràng cho TaiKhoan và SHALL không làm lộ chi tiết khóa.
7. WHERE một TaiKhoan không cấu hình KhoaApiNguoiDung cho một vai trò, THE He_Thong SHALL dùng khóa hệ thống mặc định nếu có; IF không có khóa hệ thống mặc định cho vai trò đó, THEN THE He_Thong SHALL trả về thông báo cho biết cần cấu hình khóa và SHALL không gọi nhà cung cấp.

### Requirement 23: Timeout và giới hạn vận hành cấu hình được (quản trị)

**User Story:** Là một quản trị viên, tôi muốn cấu hình các giới hạn vận hành như timeout gọi LLM, thời hạn phiên và giới hạn kích thước tệp, để điều chỉnh hệ thống theo môi trường mà không cần sửa mã.

#### Acceptance Criteria

1. WHERE một TaiKhoan có VaiTro QUAN_TRI, WHEN TaiKhoan đó cấu hình llm_timeout (mặc định 30 giây), thời hạn PhienXacThuc (mặc định 60 phút) hoặc giới hạn kích thước tệp mỗi tệp (mặc định 50MB) với giá trị trong khoảng hợp lệ, THE He_Thong SHALL cập nhật giá trị tương ứng và áp dụng mà không cần sửa mã.
2. IF một quản trị viên cấu hình llm_timeout, thời hạn PhienXacThuc hoặc giới hạn kích thước tệp với giá trị nằm ngoài khoảng hợp lệ, THEN THE He_Thong SHALL từ chối yêu cầu và giữ nguyên giá trị hiện có.
3. WHEN He_Thong áp dụng các trần thời gian của Requirement 8, thời hạn PhienXacThuc của Requirement 2 mục 1 và giới hạn kích thước tệp của Requirement 5 mục 3, THE He_Thong SHALL dùng các giá trị cấu hình ở mục 1 thay vì giá trị cố định trong mã, giữ nguyên các giá trị mặc định đã nêu.

### Requirement 24: Giới hạn tần suất truy vấn

**User Story:** Là một quản trị viên, tôi muốn giới hạn tần suất truy vấn theo từng người dùng, để bảo vệ hệ thống và nhà cung cấp LLM khỏi lạm dụng.

#### Acceptance Criteria

1. THE He_Thong SHALL giới hạn tần suất truy vấn theo từng TaiKhoan với hạn mức tần suất cấu hình được bởi TaiKhoan VaiTro QUAN_TRI.
2. IF một TaiKhoan vượt quá hạn mức tần suất truy vấn, THEN THE He_Thong SHALL từ chối tạm thời yêu cầu với thông báo lỗi về vượt giới hạn tần suất và SHALL không gọi LLM_Provider.

### Requirement 25: Quản lý mật khẩu và vòng đời phiên mở rộng

**User Story:** Là một người dùng, tôi muốn đổi mật khẩu, đặt lại mật khẩu khi quên, làm mới phiên và tự xóa tài khoản, để kiểm soát đầy đủ vòng đời tài khoản của mình.

#### Acceptance Criteria

1. WHEN một TaiKhoan đã đăng nhập yêu cầu đổi mật khẩu với mật khẩu hiện tại đúng và mật khẩu mới dài 8–64 ký tự, THE Auth_Service SHALL cập nhật mật khẩu ở dạng đã băm và SHALL chấm dứt các PhienXacThuc khác của TaiKhoan đó.
2. WHEN một người dùng yêu cầu đặt lại mật khẩu cho một email đã đăng ký, THE Auth_Service SHALL gửi một liên kết đặt lại có thời hạn tới email đó.
3. IF một người dùng yêu cầu đặt lại mật khẩu cho một email không tồn tại, THEN THE Auth_Service SHALL phản hồi chung chung không tiết lộ email đó có tồn tại hay không.
4. WHEN một liên kết đặt lại còn hạn được dùng với mật khẩu mới dài 8–64 ký tự, THE Auth_Service SHALL cập nhật mật khẩu và vô hiệu hóa liên kết đặt lại đó.
5. WHEN một TaiKhoan yêu cầu làm mới phiên trước khi PhienXacThuc hết hạn, THE Auth_Service SHALL cấp token phiên mới trong giới hạn an toàn mà không bắt TaiKhoan đăng nhập lại.
6. WHEN một TaiKhoan xác nhận yêu cầu tự xóa tài khoản của mình, THE He_Thong SHALL xóa hoặc ẩn danh dữ liệu thuộc TaiKhoan đó gồm KhongGianTaiLieu, TaiLieu, Chunk, LichSuTroChuyen và KhoaApiNguoiDung, và chấm dứt mọi PhienXacThuc của TaiKhoan đó.

### Requirement 26: Ứng dụng di động native đa nền tảng (iOS + Android)

**User Story:** Là một người dùng di động, tôi muốn một ứng dụng native trên iOS và Android để thực hiện các luồng cốt lõi giống như trên web, để dùng nền tảng ngay trên điện thoại mà không cần mở trình duyệt.

#### Acceptance Criteria

1. THE Mobile_App SHALL chạy như một ứng dụng native trên iOS và Android, dựng giao diện bằng component native của hệ điều hành, và SHALL không nhúng Web_App qua webview hay iframe.
2. THE Mobile_App SHALL cho phép một người dùng đăng ký, đăng nhập và đăng xuất bằng cách gọi đúng các endpoint xác thực hiện có, không yêu cầu endpoint backend mới.
3. WHEN một TaiKhoan đã xác thực mở Mobile_App, THE Mobile_App SHALL hiển thị danh sách KhongGianTaiLieu mà TaiKhoan đó có quyền bằng cách gọi endpoint liệt kê KhongGianTaiLieu hiện có, trong vòng 5 giây.
4. THE Mobile_App SHALL cho phép một TaiKhoan tải lên và xem trước TaiLieu trong một KhongGianTaiLieu mà TaiKhoan đó có quyền ghi, dùng đúng các endpoint hiện có.
5. THE Mobile_App SHALL cho phép một TaiKhoan gửi câu hỏi truy vấn (dài 1–1000 ký tự) trong một KhongGianTaiLieu mà TaiKhoan đó có quyền, dùng đúng endpoint truy vấn hiện có.
6. THE Mobile_App SHALL cho phép một TaiKhoan xem LichSuTroChuyen của chính mình trong một KhongGianTaiLieu mà TaiKhoan đó có quyền, dùng đúng endpoint lịch sử hiện có.
7. WHERE một TaiKhoan có VaiTro QUAN_TRI, THE Mobile_App SHALL cung cấp các chức năng quản trị tương ứng (quản lý TaiKhoan, HanMuc, MauPrompt, giới hạn vận hành) bằng cách gọi các endpoint quản trị hiện có.
8. WHERE một TaiKhoan có VaiTro NGUOI_DUNG, THE Mobile_App SHALL ẩn hoặc chặn truy cập các màn hình quản trị và điều hướng TaiKhoan đó về màn hình danh sách KhongGianTaiLieu.
9. THE Mobile_App SHALL tiêu thụ đúng REST API hiện có với header `Authorization: Bearer <token>` và SHALL không yêu cầu thêm bất kỳ endpoint backend mới nào.
10. IF đăng nhập thất bại hoặc một yêu cầu trả về token không hợp lệ hoặc hết hạn, THEN THE Mobile_App SHALL hiển thị thông báo lỗi tương ứng và điều hướng người dùng về màn hình đăng nhập.
11. IF người dùng nhập dữ liệu không hợp lệ ở các form (đăng ký, đăng nhập, truy vấn), THEN THE Mobile_App SHALL từ chối gửi yêu cầu và hiển thị thông báo lỗi nêu rõ trường không hợp lệ.
12. IF việc tải danh sách KhongGianTaiLieu thất bại, THEN THE Mobile_App SHALL hiển thị thông báo lỗi và cho phép TaiKhoan thử lại.

### Requirement 27: Lưu token an toàn trên thiết bị và xử lý 401 trên Mobile

**User Story:** Là một người dùng di động, tôi muốn token đăng nhập của tôi được lưu an toàn trên thiết bị và phiên hết hạn được xử lý đúng, để bảo vệ tài khoản của tôi trên điện thoại.

#### Acceptance Criteria

1. WHEN Mobile_App lưu token của một PhienXacThuc, THE Mobile_App SHALL lưu token vào Kho_Token_Thiet_Bi (kho lưu trữ có mã hóa của hệ điều hành) và SHALL không ghi token vào bất kỳ nơi lưu trữ không mã hóa nào, gồm localStorage, sessionStorage, tệp văn bản thuần và log.
2. IF việc ghi token vào Kho_Token_Thiet_Bi thất bại, THEN THE Mobile_App SHALL không lưu token ở bất kỳ nơi nào khác, SHALL giữ nguyên trạng thái chưa đăng nhập (không tạo PhienXacThuc), và SHALL hiển thị thông báo lỗi cho biết không lưu được phiên đăng nhập.
3. WHEN Mobile_App nhận một phản hồi có mã trạng thái HTTP 401 từ backend, THE Mobile_App SHALL xóa toàn bộ token đang lưu khỏi Kho_Token_Thiet_Bi trong vòng 2 giây kể từ khi nhận phản hồi, đồng nhất với hành vi xóa token khi 401 của Web_App.
4. WHEN Mobile_App đã hoàn tất xóa token do nhận mã trạng thái HTTP 401, THE Mobile_App SHALL điều hướng người dùng về màn hình đăng nhập.
5. IF việc xóa token khỏi Kho_Token_Thiet_Bi khi xử lý 401 thất bại, THEN THE Mobile_App SHALL vẫn điều hướng người dùng về màn hình đăng nhập và SHALL chặn mọi yêu cầu tiếp theo sử dụng token đó cho đến khi xóa thành công.
6. WHEN một người dùng đăng xuất trên Mobile_App, THE Mobile_App SHALL xóa toàn bộ token khỏi Kho_Token_Thiet_Bi và điều hướng về màn hình đăng nhập.
7. WHEN Mobile_App ghi bất kỳ log entry nào, THE Mobile_App SHALL loại trừ giá trị token khỏi nội dung log (nhất quán với Requirement 14 mục 4).

### Requirement 28: Tải tài liệu từ thiết bị qua bộ chọn tệp native

**User Story:** Là một người dùng di động, tôi muốn chọn tệp từ bộ nhớ thiết bị và tải lên một không gian, để hệ thống có thể trả lời câu hỏi dựa trên tài liệu của tôi ngay từ điện thoại.

#### Acceptance Criteria

1. WHEN một TaiKhoan chọn tải lên một TaiLieu trên Mobile_App, THE Mobile_App SHALL mở bộ chọn tệp native của thiết bị cho phép TaiKhoan chọn đúng một tệp đơn từ bộ nhớ thiết bị.
2. WHEN một tệp được chọn trên Mobile_App, THE Mobile_App SHALL gửi tệp đó tới endpoint tải lên hiện có dưới cùng định dạng multipart mà Web_App dùng, không yêu cầu endpoint backend mới.
3. IF TaiKhoan đóng hoặc hủy bộ chọn tệp native mà không chọn tệp nào, THEN THE Mobile_App SHALL không gửi bất kỳ request tải lên nào và SHALL giữ nguyên màn hình hiện tại mà không hiển thị lỗi.
4. IF backend từ chối tệp vì vượt giới hạn kích thước tệp (Requirement 5 mục 3) hoặc vì định dạng không được hỗ trợ (Requirement 5 mục 4), THEN THE Mobile_App SHALL hiển thị thông báo lỗi tương ứng do backend trả về và SHALL áp dụng đúng các giới hạn do backend thực thi mà không tự nới lỏng.
5. WHILE một tệp đang được tải lên trên Mobile_App, THE Mobile_App SHALL hiển thị chỉ báo tiến trình dạng phần trăm từ 0% đến 100%, cập nhật theo lượng dữ liệu đã gửi.
6. WHEN backend trả về kết quả tải lên thành công cho tệp đã gửi, THE Mobile_App SHALL hiển thị thông báo thành công cho TaiKhoan và SHALL kết thúc chỉ báo tiến trình.
7. IF kết nối mạng thất bại hoặc bị gián đoạn trong khi đang tải lên, THEN THE Mobile_App SHALL hiển thị thông báo lỗi cho biết tải lên không hoàn tất, SHALL kết thúc chỉ báo tiến trình, và SHALL cho phép TaiKhoan thử lại mà không để lại trạng thái tải lên treo.

### Requirement 29: Nhất quán hành vi giữa Web và Mobile qua hợp đồng API dùng chung

**User Story:** Là một người dùng dùng cả web lẫn di động, tôi muốn hai client hành xử nhất quán, để cùng một thao tác cho cùng một kết quả bất kể tôi dùng nền tảng nào.

#### Acceptance Criteria

1. THE Web_App và THE Mobile_App SHALL dùng chung cùng một tập kiểu dữ liệu TypeScript và cùng một Hop_Dong_API_Dung_Chung (cùng tập endpoint, cùng tên trường, cùng kiểu trường), và SHALL không định nghĩa endpoint hoặc trường đặc thù riêng theo nền tảng.
2. WHEN Mobile_App hoặc Web_App gửi một yêu cầu với cùng endpoint và cùng đầu vào, THE He_Thong SHALL thực thi cùng các quy tắc xác thực, ủy quyền, cô lập dữ liệu, HanMuc và truy vấn ở backend, không có nhánh logic riêng theo client.
3. WHEN cùng một TaiKhoan thực hiện cùng một thao tác với cùng đầu vào trên Web_App và trên Mobile_App, THE He_Thong SHALL trả về cùng trạng thái kết quả, cùng cấu trúc DTO và cùng giá trị nghiệp vụ (nội dung KetQuaTraLoi, nhãn độ tin cậy, nhãn xác minh, và danh sách TrichDan trùng khớp về số lượng, thứ tự và nội dung từng mục), không tính các trường không tất định như định danh phiên hay timestamp.
4. WHEN backend trả về một KetQuaTraLoi kèm danh sách TrichDan gồm N mục, THE Mobile_App SHALL diễn giải DTO đó theo đúng cùng định nghĩa kiểu trong Hop_Dong_API_Dung_Chung với Web_App, ánh xạ mỗi marker `[n]` với 1 ≤ n ≤ N tới đúng TrichDan ở vị trí tương ứng.
5. IF một marker `[n]` trong câu trả lời có n < 1 hoặc n > N (không có TrichDan tương ứng), THEN THE Web_App và THE Mobile_App SHALL xử lý đồng nhất bằng cách hiển thị marker đó dưới dạng văn bản thường, không tạo liên kết hỏng, và không ảnh hưởng phần nội dung còn lại.
6. WHEN backend trả về lỗi cho một yêu cầu, THE Web_App và THE Mobile_App SHALL nhận diện cùng loại lỗi (xác thực, đầu vào không hợp lệ, vượt HanMuc, lỗi hệ thống) và hiển thị thông báo thất bại đúng loại đó trong khi giữ nguyên dữ liệu người dùng đã nhập để thử lại.

### Requirement 30: Ghi log tập trung phía Mobile

**User Story:** Là một người vận hành, tôi muốn ứng dụng di động ghi log tập trung và không lộ dữ liệu nhạy cảm, để giám sát và truy vết sự cố nhất quán với phần còn lại của hệ thống.

#### Acceptance Criteria

1. THE Mobile_App SHALL ghi toàn bộ log qua một module logger tập trung dùng chung với một nguồn cấu hình duy nhất cho mọi màn hình và hook, và SHALL không phát sinh bất kỳ lệnh in console trực tiếp nào (số lệnh in console trực tiếp ngoài module logger = 0).
2. WHEN Mobile_App ghi bất kỳ log entry nào, THE Mobile_App SHALL bao gồm tối thiểu các trường: timestamp (gồm ngày, giờ và thông tin múi giờ), log level, tên màn hình/nguồn phát sinh và message mô tả, trong đó không trường bắt buộc nào được rỗng hoặc null.
3. THE Mobile_App SHALL chỉ dùng đúng bốn mức log: ERROR (lỗi làm gián đoạn luồng), WARN (bất thường nhưng vẫn chạy được), INFO (sự kiện nghiệp vụ quan trọng) và DEBUG (chi tiết kỹ thuật), và SHALL không ghi log ở mức nằm ngoài bốn mức này.
4. WHERE Mobile_App chạy ở môi trường production, THE Mobile_App SHALL ghi mọi log entry từ mức INFO trở lên (INFO, WARN, ERROR) và SHALL loại bỏ mọi log entry mức DEBUG; WHERE Mobile_App chạy ở môi trường phát triển, THE Mobile_App SHALL ghi mọi log entry từ mức DEBUG trở lên (DEBUG, INFO, WARN, ERROR).
5. WHEN Mobile_App ghi bất kỳ log entry nào, THE Mobile_App SHALL loại trừ hoặc che các trường nhạy cảm gồm token, mật khẩu và khóa API ở mọi cấp lồng nhau của log entry, sao cho giá trị gốc không xuất hiện ở bất kỳ phần nào (timestamp, level, nguồn, message hay dữ liệu kèm theo) của log entry (nhất quán với Requirement 14 mục 4 và Requirement 22 mục 3).
6. IF một thao tác trên Mobile_App phát sinh lỗi, THEN THE Mobile_App SHALL ghi đúng một log entry mức ERROR gồm tên thao tác, các định danh liên quan, thông điệp lỗi và stack trace, và SHALL không nuốt lỗi im lặng (không có khối catch rỗng và không có khối catch nào kết thúc mà thiếu một lời gọi ghi log mức ERROR).
7. IF module logger tập trung khởi tạo thất bại, THEN THE Mobile_App SHALL ghi một chỉ báo lỗi khởi tạo logger qua kênh dự phòng và SHALL tiếp tục vận hành mà không làm dừng ứng dụng.
