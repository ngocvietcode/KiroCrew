# Sao Khuê: chiến lược trình Ban Điều hành

Tài liệu dành cho nhóm chuẩn bị trình bày. Nội dung gồm quyết định đề nghị, thông
điệp theo từng bên liên quan, trình tự tiếp cận và các hạng mục chuẩn bị. Lập luận về
sản phẩm: [định vị](07-dinh-vi-so-voi-kiro-copilot-cowork.md). Khung tài chính:
[đề xuất đầu tư](05-de-xuat-dau-tu.md).

## 1. Bối cảnh

Ban Điều hành đã phê duyệt đầu tư Kiro và Microsoft Copilot. Đề xuất Sao Khuê cần
được đặt trong tương quan với các khoản đầu tư đó. Ba nguyên tắc định hướng cách
tiếp cận:

| Nguyên tắc | Áp dụng |
|---|---|
| Bổ sung cho danh mục hiện có | Sao Khuê phục vụ phạm vi Copilot và Kiro chưa bao phủ và gia tăng giá trị khoản đầu tư Kiro |
| Quyết định quy mô nhỏ, có thể điều chỉnh | Đề nghị một giai đoạn thí điểm có thời hạn và cổng quyết định |
| Minh bạch về giới hạn | Trình bày rủi ro và biện pháp xử lý cùng với lợi ích |

## 2. Quyết định đề nghị

Phê duyệt thí điểm Sao Khuê trong `[cần điền]` tuần, cho `[cần điền]` người dùng tại
`[cần điền]` đơn vị, trên các công việc Copilot và Kiro chưa bao phủ, với chỉ số và
ngưỡng thống nhất trước. Kết thúc thí điểm, Ban Điều hành quyết định mở rộng, điều
chỉnh hoặc dừng.

Lần trình này không đề nghị ngân sách triển khai toàn Ngân hàng. Chi phí thí điểm ở
mức thấp do nền tảng không có phí bản quyền và sử dụng năng lực mô hình sẵn có tại
cổng nội bộ.

## 3. Thông điệp chủ đạo

Copilot phục vụ công việc văn phòng trên đám mây Microsoft; Kiro phục vụ lập trình.
Công việc trên máy trạm và trong hệ thống nội bộ nằm ngoài phạm vi của agent chạy
trên đám mây nhà cung cấp. Sao Khuê phục vụ phần này dưới chính sách của VPBank.

| Luận điểm | Dữ kiện |
|---|---|
| Tồn tại khoảng trống về phạm vi | Copilot Cowork chạy trên đám mây Microsoft và thao tác trên tài liệu trong tenant Microsoft 365, không làm việc với tệp trên máy người dùng |
| Chi phí agent của nhà cung cấp tăng theo mức sử dụng | Cowork tính Copilot Credit cộng thêm vào giấy phép 30 USD; Amazon Quick tính giờ agent; Kiro tính credit |
| VPBank đã có tài sản sẵn sàng | Sao Khuê đã vận hành được: desktop, SSO, proxy công ty, cổng kiểm soát, nhật ký kiểm toán |

## 4. Thông điệp theo bên liên quan

Từng bên liên quan cần được trao đổi riêng trước buổi trình chung.

| Bên liên quan | Mối quan tâm | Thông điệp | Tài liệu |
|---|---|---|---|
| Tổng Giám đốc, HĐQT | Hiệu quả, vị thế, tương quan với khoản đầu tư Copilot | Sở hữu nền tảng bên cạnh việc sử dụng dịch vụ; giảm phụ thuộc một nhà cung cấp | [01](01-tong-quan-va-dinh-huong.md), [07](07-dinh-vi-so-voi-kiro-copilot-cowork.md) |
| Giám đốc Tài chính | Chi phí phát sinh, khả năng dự báo | Chi phí thí điểm thấp; chi phí mô hình đo tại một điểm | [05](05-de-xuat-dau-tu.md) |
| Giám đốc Công nghệ | Thêm hệ thống phải vận hành | Tận dụng giấy phép Kiro; tùy biến tách khỏi lõi; chuẩn mở | [03](03-tich-hop.md) |
| An ninh thông tin | Agent thực thi lệnh trên máy trạm | Một cổng kiểm soát và một nhật ký cho mọi agent; giới hạn trên Windows cùng đề nghị quyết định biện pháp bù | [04](04-bao-mat-va-quan-tri.md) |
| Quản trị rủi ro, Pháp chế | Luồng dữ liệu, trách nhiệm | Luồng dữ liệu do VPBank cấu hình; đề nghị xác định loại dữ liệu phải xử lý qua đường nội bộ | [04](04-bao-mat-va-quan-tri.md) |
| Lãnh đạo đơn vị thí điểm | Lợi ích cho đơn vị | Công việc cụ thể của đơn vị, đo được | [02](02-loi-ich-theo-don-vi.md) |

## 5. Trình tự tiếp cận

| Bước | Nội dung | Kết quả cần đạt |
|---|---|---|
| 1 | Làm việc với An ninh thông tin trên cơ sở tài liệu [04](04-bao-mat-va-quan-tri.md), gồm cả phần giới hạn | An ninh thông tin xác lập phạm vi và điều kiện thí điểm |
| 2 | Đề nghị Pháp chế và Quản trị rủi ro xác định có loại dữ liệu nào không được xử lý trên hạ tầng nhà cung cấp nước ngoài | Ý kiến bằng văn bản |
| 3 | Xác định người bảo trợ cấp Ban Điều hành và hai đơn vị thí điểm thuộc khối công nghệ hoặc vận hành | Cam kết của người bảo trợ và lãnh đạo đơn vị |
| 4 | Chuẩn bị buổi trình diễn trong mạng VPBank | Kịch bản chạy ổn định |
| 5 | Trình Ban Điều hành | Quyết định phê duyệt thí điểm |
| 6 | Báo cáo kết quả thí điểm bằng số đo thực | Quyết định mở rộng, điều chỉnh hoặc dừng |

Bước 1 có tính quyết định. Ý kiến của An ninh thông tin cần có trước buổi trình
chung.

Ý kiến tại bước 2 xác định trọng tâm lập luận. Nếu tồn tại loại dữ liệu phải xử lý
qua đường nội bộ, đây là căn cứ mạnh nhất cho Sao Khuê và đến từ một đơn vị độc lập.
Nhóm đề xuất không tự diễn giải quy định pháp lý.

## 6. Kịch bản trình diễn

Kịch bản thể hiện công việc các sản phẩm đã mua chưa thực hiện được, chạy thực tế
trong mạng VPBank:

1. Đăng nhập bằng SSO của VPBank.
2. Giao một công việc trên hệ thống nội bộ qua máy chủ MCP.
3. Agent yêu cầu quyền chạy lệnh; người trình bày phê duyệt trên thẻ phê duyệt.
4. Agent bị chặn khi thực hiện thao tác ngoài chính sách.
5. Nhật ký kiểm toán hiển thị đầy đủ các bước.
6. Công việc được đặt lịch chạy hằng ngày.

Bước 4 và 5 là trọng tâm đối với Ban Điều hành: hành vi của agent được kiểm soát và
có dấu vết.

## 7. Cấu trúc buổi trình

| Phần | Nội dung | Tỉ trọng thời gian |
|---|---|---|
| Mở đầu | Ghi nhận khoản đầu tư Copilot và Kiro; phạm vi chưa được bao phủ | Ngắn |
| Trình diễn | Kịch bản tại mục 6 | Lớn nhất |
| Định vị | Bảng phân vai công cụ | Ngắn |
| Rủi ro | Giới hạn hiện tại và biện pháp đã thống nhất với An ninh thông tin | Ngắn |
| Quyết định đề nghị | Thí điểm, chỉ số, cổng quyết định | Ngắn |

## 8. Câu hỏi dự kiến

| Câu hỏi | Trả lời |
|---|---|
| VPBank đã có Copilot, vì sao cần thêm Sao Khuê? | Copilot phục vụ công việc trong Microsoft 365. Sao Khuê phục vụ công việc trên máy trạm và hệ thống nội bộ. Bảng phân vai cho thấy hai phạm vi tách biệt. |
| Kích hoạt Cowork có đáp ứng được không? | Cowork chạy trên đám mây Microsoft, trên tài liệu trong tenant. Kho mã, log và hệ thống nội bộ nằm ngoài phạm vi này. Cowork tính credit theo mức sử dụng. |
| Vì sao không sử dụng Amazon Quick? | Quick mạnh về phân tích và tự động hóa trên dữ liệu AWS và SaaS, tính phí theo người dùng và giờ agent. Sản phẩm này cần được đánh giá riêng nếu VPBank có nhu cầu đó; phạm vi máy trạm và mạng nội bộ vẫn cần giải pháp khác. |
| Đơn vị nào chịu trách nhiệm khi có sự cố? | VPBank, thông qua đội nền tảng. Chi phí này nằm trong đề xuất; đổi lại là quyền kiểm soát mã nguồn và dữ liệu. |
| Agent chạy lệnh trên máy nhân viên có an toàn? | Mọi lệnh qua cổng kiểm soát và phê duyệt, có nhật ký. Windows chưa có sandbox hệ điều hành; biện pháp bù cho thí điểm do An ninh thông tin xác lập. |
| Mức tiết kiệm dự kiến? | Chưa có số đo tại VPBank. Thí điểm cung cấp số liệu này. |
| Nếu dự án mã nguồn mở ngừng phát triển? | VPBank giữ toàn bộ mã nguồn theo giấy phép Apache 2.0 và có thể tự duy trì. |
| Vì sao không chờ nhà cung cấp? | Định hướng sản phẩm của các nhà cung cấp là agent trên đám mây của họ. Máy trạm và mạng nội bộ của ngân hàng nằm ngoài định hướng đó. |

## 9. Nguyên tắc khi trình bày

- Ghi nhận giá trị của Copilot và Kiro; không so sánh theo hướng phủ định.
- Chỉ sử dụng số liệu có căn cứ; không cam kết con số năng suất trước thí điểm.
- Chủ động nêu giới hạn trên Windows.
- Dẫn ý kiến của Pháp chế đối với yêu cầu pháp lý.
- Giới hạn đề nghị ở quy mô thí điểm.
- Đưa nội dung kiến trúc vào phụ lục.

## 10. Hạng mục chuẩn bị

| Hạng mục | Phụ trách | Trạng thái |
|---|---|---|
| An ninh thông tin chấp thuận phạm vi và điều kiện thí điểm | `[cần điền]` | `[cần điền]` |
| Ý kiến của Pháp chế về dữ liệu | `[cần điền]` | `[cần điền]` |
| Người bảo trợ cấp Ban Điều hành | `[cần điền]` | `[cần điền]` |
| Đơn vị và công việc thí điểm, số đo nền | `[cần điền]` | `[cần điền]` |
| Cấu hình sản xuất: địa chỉ cổng, client OIDC chính thức | `[cần điền]` | `[cần điền]` |
| Kịch bản trình diễn chạy ổn định trong mạng VPBank | `[cần điền]` | `[cần điền]` |
| Đối chiếu giá và điều khoản Copilot, Cowork, Kiro theo hợp đồng VPBank | `[cần điền]` | `[cần điền]` |
| Ngân sách và thời gian thí điểm | `[cần điền]` | `[cần điền]` |
