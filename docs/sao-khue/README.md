# Bộ tài liệu Sao Khuê (nội bộ VPBank)

Bộ tài liệu là nguồn tham chiếu chính thức về Sao Khuê, nền tảng trợ lý AI tự hành
của VPBank. Mỗi tài liệu phục vụ một nhóm người đọc và một mục đích.

## Danh mục

| Tài liệu | Người đọc | Mục đích |
|---|---|---|
| [01 - Tổng quan và định hướng](01-tong-quan-va-dinh-huong.md) | Ban Điều hành, lãnh đạo khối | Giá trị chiến lược, hiện trạng, điều kiện tiên quyết, lộ trình |
| [02 - Lợi ích theo đơn vị](02-loi-ich-theo-don-vi.md) | Lãnh đạo khối, đầu mối nghiệp vụ | Hiện trạng, giải pháp và chỉ số đo lường theo từng đơn vị |
| [03 - Tích hợp](03-tich-hop.md) | Kiến trúc sư, đơn vị có hệ thống cần kết nối | Các điểm tích hợp và quy trình đưa hệ thống nội bộ vào nền tảng |
| [04 - Bảo mật và quản trị](04-bao-mat-va-quan-tri.md) | An ninh thông tin, Quản trị rủi ro, Kiểm toán nội bộ | Mô hình kiểm soát, vị trí dữ liệu, giới hạn hiện tại, danh mục thẩm định |
| [05 - Đề xuất đầu tư](05-de-xuat-dau-tu.md) | Ban Điều hành, Khối Tài chính | Phân tích phương án, hạng mục đầu tư, lộ trình, chỉ số, rủi ro |
| [06 - Hỏi đáp](06-hoi-dap.md) | Người trình bày | Câu trả lời cho các câu hỏi thường gặp |
| [07 - Định vị so với Kiro, Copilot, Cowork, Amazon Quick](07-dinh-vi-so-voi-kiro-copilot-cowork.md) | Ban Điều hành, người trình bày | Vai trò của Sao Khuê trong danh mục sản phẩm AI của VPBank |
| [08 - Chiến lược trình Ban Điều hành](08-chien-luoc-thuyet-phuc-ban-lanh-dao.md) | Nhóm chuẩn bị trình bày | Quyết định đề nghị, thông điệp theo bên liên quan, trình tự, kịch bản trình diễn |
| [Kiến trúc kỹ thuật chi tiết](../architecture/sao-khue-architecture.md) | Kiến trúc sư, kỹ sư | Kiến trúc ở mức mã nguồn |

## Lộ trình đọc theo vai trò

| Vai trò | Tài liệu |
|---|---|
| Ban Điều hành | 01, 07, 05 |
| Lãnh đạo khối | 01, 02 |
| An ninh thông tin, Quản trị rủi ro | 04, 03 |
| Nhóm chuẩn bị trình bày | 08, 07, 06 |

## Nguyên tắc duy trì

- Mã nguồn là căn cứ gốc. Khi tài liệu khác mã nguồn, tài liệu được cập nhật trong
  cùng thay đổi.
- Số liệu của VPBank (chi phí, số người dùng, thời gian tiết kiệm) chỉ được ghi khi
  đã đo hoặc đã được đơn vị có trách nhiệm xác nhận. Trước đó ghi `[cần điền]`.
- Mỗi chủ đề có một tài liệu. Nội dung đã có ở tài liệu khác được dẫn liên kết.
- Mỗi tài liệu phân biệt năng lực đã có trong bản dựng với năng lực theo kế hoạch.
- Thông tin về sản phẩm của nhà cung cấp kèm nguồn và thời điểm tra cứu.

## Thuật ngữ

| Thuật ngữ | Định nghĩa |
|---|---|
| Sao Khuê Gateway | Tiến trình điều phối chạy trên máy người dùng hoặc máy chủ: giao diện, phiên, bộ nhớ, lập lịch, cổng kiểm soát quyền |
| `saokhue-cli` | Máy chủ ACP tích hợp sẵn, kết nối cổng AI LLM nội bộ của VPBank hoặc cổng tương thích OpenAI |
| ACP | Agent Client Protocol, giao thức JSON-RPC qua stdio giữa Gateway và bộ máy agent |
| Bộ máy agent (harness) | Thành phần thực thi agent phía sau Gateway (`saokhue-cli`, Kiro CLI và các bộ máy khác), lựa chọn tại `agent.acp_backend` |
| MCP | Model Context Protocol, chuẩn cung cấp công cụ và dữ liệu cho agent |
| Crew | Đội agent gồm một điều phối viên và nhiều agent thực thi |
| Edition | Lớp tùy biến giao diện của VPBank tại `editions/saokhue/`, ghép vào lõi qua điểm mở rộng |
