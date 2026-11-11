# Sao Khuê: hỏi đáp

Tài liệu dành cho người trình bày. Mỗi câu hỏi có câu trả lời ngắn và tài liệu tham
chiếu. Nội dung chưa có số liệu được ghi rõ là chưa có.

## 1. Về sản phẩm

| Câu hỏi | Trả lời |
|---|---|
| Sao Khuê khác trợ lý hội thoại nội bộ ở điểm nào? | Sao Khuê thực hiện công việc: đọc và sửa tệp, chạy lệnh, gọi hệ thống qua công cụ, chạy theo lịch, phân việc cho nhiều agent và lưu giữ bối cảnh qua các phiên. Xem [tổng quan](01-tong-quan-va-dinh-huong.md). |
| Sao Khuê do VPBank tự phát triển hay mua? | Nền tảng gốc là Kiro Crew, mã nguồn mở theo giấy phép Apache 2.0. VPBank phát triển thêm `saokhue-cli`, đăng nhập SSO, kết nối proxy công ty và giao diện riêng. VPBank giữ toàn bộ mã nguồn. |
| Sao Khuê sử dụng mô hình AI nào? | Các mô hình do cổng AI LLM nội bộ cung cấp. `saokhue-cli` dùng chuẩn tương thích OpenAI nên việc thay đổi mô hình được thực hiện tại cổng. |
| Đối tượng sử dụng có giới hạn ở lập trình viên? | Không. Năng lực mạnh nhất hiện nay thuộc về công việc kỹ thuật và vận hành. Ứng dụng desktop, lập lịch và kỹ năng áp dụng được cho nghiệp vụ. Xem [lợi ích theo đơn vị](02-loi-ich-theo-don-vi.md). |
| Có sử dụng được trên điện thoại? | Bản Sao Khuê ẩn chức năng kết nối điện thoại. |

## 2. Về dữ liệu và bảo mật

| Câu hỏi | Trả lời |
|---|---|
| Dữ liệu của Ngân hàng có ra ngoài không? | Nội dung hội thoại được gửi tới cổng LLM đã cấu hình, mặc định là cổng nội bộ. Lịch sử, bộ nhớ và tri thức lưu trên máy người dùng. Đo lường tắt theo mặc định. Luồng dữ liệu phía sau cổng cần đơn vị vận hành cổng xác nhận. |
| Agent có thể tự xóa tệp hoặc chạy lệnh nguy hiểm? | Mọi lệnh gọi công cụ qua cổng kiểm soát: chính sách, danh sách lệnh cấm, đường dẫn nhạy cảm và phê duyệt của người dùng. Linux và macOS có thêm sandbox hệ điều hành. Windows chưa có; xem [bảo mật và quản trị](04-bao-mat-va-quan-tri.md). |
| Agent có tự nới quyền được không? | Không. Tệp trần quyền ở chế độ chỉ đọc đối với agent; chính sách cục bộ chỉ có thể siết chặt thêm chính sách tập trung. |
| Có truy vết được thao tác? | Có. Thao tác nhạy cảm được ghi nhật ký kiểm toán có chữ ký HMAC, bao gồm đăng nhập và thay đổi cấu hình proxy. |
| Mật khẩu proxy và token lưu ở đâu? | Xác thực proxy bằng tài khoản Windows không lưu mật khẩu. Với Basic, mật khẩu lưu trong kho bí mật. Token SSO lưu trong tệp riêng với quyền đọc giới hạn cho chủ sở hữu. |
| Xử lý rủi ro lệnh cài cắm trong tài liệu hoặc kho mã (prompt injection) thế nào? | Lớp phòng vệ là cổng kiểm soát và phê duyệt. Thao tác có tác động cần đặt ở chế độ yêu cầu phê duyệt. Kiểm thử prompt injection thuộc danh mục thẩm định. |

## 3. Về vận hành và chi phí

| Câu hỏi | Trả lời |
|---|---|
| Sao Khuê chạy ở đâu? | Trên máy trạm người dùng qua ứng dụng desktop. Nền tảng cũng chạy được trên máy chủ Linux cho công việc cần hoạt động liên tục. |
| Chi phí bao nhiêu? | Nền tảng không có phí bản quyền. Chi phí gồm năng lực mô hình, đội duy trì, thẩm định và đào tạo. Số liệu của VPBank chưa có; xem [đề xuất đầu tư](05-de-xuat-dau-tu.md). |
| Tiết kiệm được bao nhiêu thời gian? | Chưa có số đo tại VPBank. Giai đoạn thí điểm sẽ cung cấp số liệu. |
| Đơn vị nào hỗ trợ khi có lỗi? | Đội nền tảng nội bộ. Đây là hạng mục đầu tư trong đề xuất. |
| Cập nhật từ bản gốc có ảnh hưởng phần của VPBank? | Phần giao diện riêng nằm tại `editions/saokhue/` và ghép qua điểm mở rộng. Phần backend riêng là các module tách biệt. |
| Nếu dự án gốc ngừng phát triển? | VPBank giữ toàn bộ mã nguồn theo giấy phép cho phép tự duy trì. Rủi ro là phải tự đảm nhận việc phát triển tiếp. |

## 4. Về tích hợp và định vị

| Câu hỏi | Trả lời |
|---|---|
| Đơn vị muốn agent sử dụng hệ thống của mình cần làm gì? | Cung cấp hệ thống dưới dạng máy chủ MCP, đăng ký vào registry, khai báo chính sách. Xem [tích hợp](03-tich-hop.md), mục 4. |
| Có bắt buộc dùng `saokhue-cli`? | Về kỹ thuật, nền tảng chọn được 11 bộ máy agent. Chỉ `saokhue-cli` mặc định đi qua cổng nội bộ; việc cho phép bộ máy nào thuộc thẩm quyền chính sách. |
| Lãnh đạo cần endpoint riêng có đáp ứng được? | Có. `saokhue-cli` nhận địa chỉ cổng, khóa và mô hình riêng theo người dùng, giữ nguyên cổng kiểm soát và bộ nhớ. |
| VPBank đã có Copilot và Kiro, vì sao cần Sao Khuê? | Copilot phục vụ công việc trong Microsoft 365, Kiro phục vụ lập trình. Sao Khuê phục vụ công việc trên máy trạm và hệ thống nội bộ. Xem [định vị](07-dinh-vi-so-voi-kiro-copilot-cowork.md). |
