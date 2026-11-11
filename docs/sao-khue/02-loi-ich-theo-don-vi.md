# Sao Khuê: lợi ích theo đơn vị

Tài liệu dành cho lãnh đạo các khối và đầu mối nghiệp vụ. Với từng đơn vị, tài liệu
trình bày hiện trạng, giải pháp và chỉ số đo lường đề xuất. Các tình huống sử dụng
dựa trên năng lực hiện có của nền tảng. Số liệu hiệu quả tại VPBank sẽ được bổ sung
sau giai đoạn thí điểm.

## 1. Lợi ích áp dụng cho mọi đơn vị

| Lợi ích | Mô tả |
|---|---|
| Liên tục về bối cảnh | Thông tin dự án, quyết định và cách làm việc được lưu giữ qua các phiên |
| Giảm việc lặp lại | Báo cáo định kỳ, theo dõi và rà soát chạy theo lịch, chỉ báo cáo khi cần người xem xét |
| Chuẩn hóa quy trình | Quy trình của đơn vị được đóng gói thành kỹ năng dùng chung; nhân sự mới áp dụng đúng chuẩn ngay |
| Kênh sử dụng AI chính thức | Đăng nhập bằng tài khoản VPBank, dữ liệu đi qua cổng nội bộ |

## 2. Khối Công nghệ: phát triển phần mềm và kiểm thử

| Hiện trạng | Giải pháp | Năng lực nền tảng |
|---|---|---|
| Hạng mục lớn phụ thuộc vào một cá nhân | Điều phối viên phân rã mục tiêu, nhiều agent thực hiện song song trên các nhánh Git riêng | Crew, subagent, Git worktree |
| Rà soát mã và viết kiểm thử chiếm nhiều thời gian | Agent đọc mã, đề xuất chỉnh sửa, chạy kiểm thử; kỹ sư duyệt kết quả | Công cụ tệp và lệnh, phê duyệt từng thao tác |
| Tác vụ dài bị gián đoạn phải thực hiện lại | Tác vụ nhiều bước có điểm khôi phục | Task runner |
| Chuẩn viết mã áp dụng không đồng đều | Chuẩn của đơn vị được đóng gói thành kỹ năng | Skills |

Chỉ số đề xuất: thời gian từ yêu cầu đến bản hợp nhất, số lỗi phát hiện trước phát
hành, tỉ lệ thay đổi có kiểm thử. Giá trị: `[cần điền]`.

## 3. Vận hành, hạ tầng và RPA

| Hiện trạng | Giải pháp | Năng lực nền tảng |
|---|---|---|
| Giám sát thủ công, phát hiện chậm | Kiểm tra định kỳ, cảnh báo khi có bất thường | Cron, heartbeat, monitoring |
| Phân tích log và sự cố tốn nhiều công | Agent đọc log, tổng hợp nguyên nhân, đề xuất bước xử lý | Công cụ tệp và lệnh, bộ nhớ về sự cố trước |
| Quy trình vận hành phụ thuộc kinh nghiệm cá nhân | Runbook được đóng gói thành kỹ năng; agent thực hiện và ghi nhận | Skills, nhật ký kiểm toán |

Chỉ số đề xuất: thời gian phát hiện và khắc phục sự cố, số công việc lặp lại được tự
động hóa. Giá trị: `[cần điền]`.

## 4. Dữ liệu và phân tích

| Hiện trạng | Giải pháp | Năng lực nền tảng |
|---|---|---|
| Báo cáo định kỳ lập thủ công | Script đã kiểm duyệt chạy theo lịch và tổng hợp kết quả | Cron chạy script Python |
| Hệ thống dữ liệu nội bộ chưa kết nối được với AI | Đơn vị cung cấp hệ thống dưới dạng máy chủ MCP | MCP, registry MCP doanh nghiệp |
| Tài liệu tham chiếu phân tán | Thư viện tri thức cục bộ, tra cứu theo ngữ nghĩa | Knowledge, bộ nhớ lai |

Chỉ số đề xuất: thời gian hoàn thành một báo cáo, số báo cáo tự động. Giá trị:
`[cần điền]`.

## 5. Các khối nghiệp vụ

| Hiện trạng | Giải pháp | Năng lực nền tảng |
|---|---|---|
| Soạn văn bản, tờ trình, bản trình chiếu tốn thời gian | Soạn thảo, rà soát, tạo tài liệu và biểu đồ trong hội thoại | Artifacts, ứng dụng soạn tài liệu và trình chiếu |
| Biên bản họp và theo dõi đầu việc | Tổng hợp nội dung, tách đầu việc, nhắc theo lịch | Ứng dụng Meetings, cron |
| Người dùng không quen dòng lệnh | Ứng dụng desktop, đăng nhập SSO | Dashboard, thẻ đăng nhập Sao Khuê |

Chỉ số đề xuất: thời gian soạn một văn bản chuẩn, số vòng chỉnh sửa. Giá trị:
`[cần điền]`.

Lưu ý: với công việc nằm trong Microsoft 365, Microsoft Copilot là công cụ phù hợp
hơn. Xem [định vị](07-dinh-vi-so-voi-kiro-copilot-cowork.md).

## 6. Cấp quản lý và lãnh đạo

- Một nhóm người dùng có thể được cấu hình endpoint mô hình riêng (địa chỉ, khóa, mô
  hình) trong khi giữ nguyên giao diện, bộ nhớ và cổng kiểm soát quyền.
- Lịch sử và ghi nhớ của người dùng lưu trên máy cá nhân, không tập trung về một kho
  chung.
- Bảng công việc của đội agent hiển thị đầu việc, trạng thái và kết quả.

## 7. An ninh thông tin, Quản trị rủi ro, Kiểm toán nội bộ

| Mối quan tâm | Đáp ứng |
|---|---|
| Nhân viên sử dụng công cụ AI ngoài tầm kiểm soát | Kênh chính thức qua cổng nội bộ, xác thực SSO |
| Agent vượt quyền | Mỗi lệnh gọi công cụ qua cổng `PreToolUse`; quyền hiệu lực là phần giao chặt nhất của chính sách và hồ sơ phiên; agent không ghi được tệp trần quyền |
| Lộ thông tin xác thực | Thư mục chứa khóa thuộc danh sách đường dẫn nhạy cảm; mật khẩu proxy chỉ lưu trong kho bí mật |
| Truy vết | Nhật ký kiểm toán có chữ ký HMAC, bao gồm thao tác đăng nhập |
| Áp dụng chính sách đồng bộ | Một nguồn chính sách tập trung; chính sách cục bộ chỉ có thể siết chặt thêm |

Giới hạn về sandbox trên Windows được trình bày tại
[bảo mật và quản trị](04-bao-mat-va-quan-tri.md).

## 8. Các bước khởi động tại đơn vị

1. Chọn hai đến ba công việc lặp lại, đo được, rủi ro thấp.
2. Ghi nhận số đo hiện tại.
3. Đóng gói quy trình thành kỹ năng và sử dụng trong một chu kỳ làm việc.
4. Đo lại và cập nhật kết quả vào tài liệu này.
