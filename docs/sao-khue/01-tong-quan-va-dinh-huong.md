# Sao Khuê: tổng quan và định hướng

Tài liệu dành cho Ban Điều hành và lãnh đạo các khối.

## 1. Tóm tắt điều hành

Sao Khuê là nền tảng trợ lý AI tự hành do VPBank sở hữu và vận hành. Nền tảng chạy
trên máy trạm và trong mạng nội bộ của Ngân hàng, xác thực bằng tài khoản VPBank, gọi
mô hình AI qua cổng AI LLM nội bộ và thực hiện được các công việc nhiều bước dưới sự
giám sát của người dùng.

Sao Khuê được phát triển trên Kiro Crew, một nền tảng mã nguồn mở theo giấy phép
Apache 2.0. VPBank đã bổ sung máy chủ agent `saokhue-cli`, đăng nhập một lần (SSO),
kết nối qua proxy công ty và giao diện mang nhận diện VPBank. Bản dựng hiện tại đã
vận hành được.

Khuyến nghị: Ban Điều hành phê duyệt một giai đoạn thí điểm có đo lường tại một số
đơn vị công nghệ và vận hành, với điều kiện An ninh thông tin hoàn tất thẩm định
trước khi khởi động.

## 2. Giá trị chiến lược đối với VPBank

| Trụ cột | Nội dung | Căn cứ trong bản dựng |
|---|---|---|
| Chủ quyền dữ liệu | Lệnh gọi mô hình đi qua cổng AI LLM nội bộ. Lịch sử, bộ nhớ và tri thức lưu trên máy người dùng. | SSO VPBank; cơ sở dữ liệu SQLite cục bộ tại `~/.vpbank/saokhue`; mô hình nhúng chạy cục bộ; đo lường tắt theo mặc định |
| Năng lực thực thi | Agent đọc và sửa tệp, chạy lệnh, gọi hệ thống, chia việc cho nhiều agent, chạy theo lịch khi người dùng vắng mặt. | Crew, task runner có điểm khôi phục, cron |
| Kiểm soát tập trung | Mọi thao tác của agent đi qua cổng kiểm soát quyền của VPBank và được ghi nhật ký kiểm toán có chữ ký HMAC. Agent không ghi đè được trần quyền của chính nó. | Cổng `PreToolUse`; phép giao chính sách doanh nghiệp và hồ sơ phiên |
| Tương thích hạ tầng ngân hàng | Nền tảng tự xác thực với proxy công ty bằng tài khoản Windows đang đăng nhập và tin cậy chứng chỉ CA nội bộ. | NTLM và Kerberos qua SSPI, không lưu mật khẩu; nạp kho chứng chỉ của hệ điều hành |
| Độc lập nhà cung cấp | Mô hình phía sau cổng có thể thay đổi mà người dùng không phải đổi công cụ. | `saokhue-cli` dùng chuẩn tương thích OpenAI; 11 bộ máy agent chọn được qua cấu hình |
| Quyền sở hữu | VPBank giữ toàn bộ mã nguồn và tiếp tục nhận cải tiến từ bản gốc. | Phần tùy biến giao diện tách riêng tại `editions/saokhue/` |

## 3. Phân biệt với trợ lý hội thoại

| Tiêu chí | Trợ lý hội thoại | Sao Khuê |
|---|---|---|
| Phạm vi | Hỏi đáp trong một cửa sổ | Thực hiện công việc trên tệp, lệnh và hệ thống |
| Trí nhớ | Kết thúc theo phiên | Bộ nhớ dài hạn, tra cứu theo từ khóa và ngữ nghĩa |
| Khi người dùng vắng mặt | Dừng | Chạy theo lịch, chạy tác vụ dài, báo cáo khi cần quyết định |
| Quy mô | Một hội thoại | Một điều phối viên phân việc cho nhiều agent chạy song song |
| Tri thức đơn vị | Nhập lại mỗi lần | Quy trình đóng gói thành kỹ năng (`SKILL.md`), dùng chung |
| Kiểm soát | Theo nhà cung cấp | Chính sách, phê duyệt và nhật ký kiểm toán của VPBank |

## 4. Hiện trạng

Các hạng mục đã hoàn thành trong bản dựng:

- Ứng dụng desktop Sao Khuê với nhận diện VPBank, đóng gói bằng
  `packaging/build-saokhue.sh`.
- `saokhue-cli`, máy chủ agent tích hợp sẵn, gồm bảy công cụ thao tác tệp và lệnh,
  kết nối được máy chủ MCP.
- Đăng nhập trên giao diện theo bốn phương thức: SSO qua trình duyệt, mã thiết bị,
  token hoặc virtual key, tên đăng nhập và mật khẩu.
- Kết nối qua proxy công ty, kèm thẻ cấu hình và chức năng kiểm tra kết nối.
- Bộ agent quản lý sẵn cho điều phối, thực thi, nghiên cứu, tri thức và agent khách
  không có quyền gọi công cụ.
- Chế độ registry MCP doanh nghiệp bật theo mặc định.

## 5. Điều kiện tiên quyết trước khi mở rộng

| Hạng mục | Hiện trạng | Yêu cầu |
|---|---|---|
| Cấu hình môi trường | Mặc định trỏ tới môi trường thử nghiệm (`portal.aws.rpa.vpbank.dev`, client ID `app:test-crew`) | Cấp địa chỉ cổng và client OIDC cho môi trường sản xuất |
| Cô lập trên Windows | Sandbox tầng hệ điều hành chỉ có trên Linux và macOS. Trên Windows, cổng kiểm soát, phê duyệt và nhật ký vẫn hoạt động nhưng tiến trình do agent khởi chạy không bị hệ điều hành cô lập. | An ninh thông tin thẩm định và quyết định biện pháp bù |
| Số liệu hiệu quả | Chưa có số đo tại VPBank | Thí điểm có đo lường trước và sau |

## 6. Lộ trình

| Giai đoạn | Mục tiêu | Điều kiện chuyển giai đoạn |
|---|---|---|
| 1. Thí điểm | Một số đơn vị công nghệ và vận hành sử dụng trên công việc thực, có đo lường | An ninh thông tin chấp thuận; đạt ngưỡng chỉ số đã thống nhất |
| 2. Mở rộng | Bổ sung đơn vị nghiệp vụ, bộ kỹ năng chuẩn theo đơn vị, phân phối chính sách tập trung | Cấu hình sản xuất, quy trình hỗ trợ và đào tạo sẵn sàng |
| 3. Nền tảng dùng chung | Các đơn vị tự tích hợp hệ thống qua MCP và tự đóng gói ứng dụng | Có đơn vị chủ quản và cơ chế phân bổ chi phí |

Thời gian và nguồn lực từng giai đoạn: `[cần điền]`.

## 7. Tài liệu liên quan

- Lợi ích theo đơn vị: [02-loi-ich-theo-don-vi.md](02-loi-ich-theo-don-vi.md)
- Định vị so với các sản phẩm đã mua: [07-dinh-vi-so-voi-kiro-copilot-cowork.md](07-dinh-vi-so-voi-kiro-copilot-cowork.md)
- Đề xuất đầu tư: [05-de-xuat-dau-tu.md](05-de-xuat-dau-tu.md)
- Kiến trúc chi tiết: [sao-khue-architecture.md](../architecture/sao-khue-architecture.md)
