# Sao Khuê: đề xuất đầu tư

Tài liệu dành cho Ban Điều hành và Khối Tài chính. Các số liệu của VPBank được để ở
dạng `[cần điền]` và chỉ được cập nhật bằng số đã đo hoặc đã được đơn vị có trách
nhiệm xác nhận.

## 1. Nội dung trình

Kính trình Ban Điều hành phê duyệt giai đoạn thí điểm Sao Khuê có đo lường, kèm cổng
quyết định trước khi mở rộng. Phạm vi, thời gian và ngân sách: `[cần điền]`.

## 2. Sự cần thiết

| Vấn đề | Hàm ý đối với VPBank |
|---|---|
| Nhu cầu sử dụng AI của nhân viên đã hình thành | Thiếu kênh chính thức cho các công việc ngoài phạm vi Microsoft 365 và IDE, nhu cầu sẽ chuyển sang công cụ Ngân hàng không kiểm soát được |
| Phần lớn thời gian làm việc dành cho thực thi | Giá trị lớn nhất đến từ agent thực hiện công việc nhiều bước dưới sự giám sát |
| Tri thức quy trình phụ thuộc vào cá nhân | Cần cơ chế đóng gói quy trình thành tài sản dùng chung của tổ chức |

## 3. Phân tích phương án

| Phương án | Ưu điểm | Hạn chế |
|---|---|---|
| Giữ nguyên hiện trạng | Không phát sinh chi phí trực tiếp | Khoảng trống về máy trạm và hệ thống nội bộ không được giải quyết |
| Mở rộng sản phẩm mua ngoài | Triển khai nhanh, có nhà cung cấp hỗ trợ | Dữ liệu xử lý trên hạ tầng nhà cung cấp; chi phí tăng theo mức sử dụng; khả năng kết nối hệ thống nội bộ hạn chế |
| Tự phát triển từ đầu | Kiểm soát hoàn toàn | Thời gian và nguồn lực lớn để đạt năng lực tương đương |
| Sao Khuê (khuyến nghị) | Kiểm soát mã nguồn và dữ liệu; không phí bản quyền nền tảng; đã vận hành được; tận dụng giấy phép Kiro hiện có | Cần đội nền tảng nội bộ; VPBank tự thẩm định và tự hỗ trợ |

So sánh chi tiết với Kiro, Microsoft Copilot, Copilot Cowork và Amazon Quick:
[07-dinh-vi-so-voi-kiro-copilot-cowork.md](07-dinh-vi-so-voi-kiro-copilot-cowork.md).

## 4. Tài sản hiện có

- Ứng dụng desktop vận hành được với nhận diện VPBank.
- `saokhue-cli` kết nối cổng AI LLM nội bộ, đăng nhập SSO theo bốn phương thức.
- Kết nối qua proxy công ty bằng tài khoản Windows và tin cậy CA nội bộ.
- Cổng kiểm soát quyền, phê duyệt, nhật ký kiểm toán, phân phối chính sách tập trung.
- Bộ nhớ dài hạn, lập lịch, tác vụ nhiều bước, đội agent.
- Phần tùy biến tách khỏi lõi, giảm chi phí cập nhật theo bản gốc.

## 5. Hạng mục đầu tư

| Hạng mục | Nội dung | Ước tính |
|---|---|---|
| Đội nền tảng | Duy trì bản VPBank, hợp nhất bản gốc, đóng gói và phát hành | `[cần điền]` |
| Năng lực mô hình | Hạn mức trên cổng AI LLM cho giai đoạn thí điểm và mở rộng | `[cần điền]` |
| Thẩm định bảo mật | Rà soát, kiểm thử xâm nhập, biện pháp bù trên Windows | `[cần điền]` |
| Cấu hình sản xuất | Địa chỉ cổng, client OIDC chính thức, nguồn chính sách, registry MCP | `[cần điền]` |
| Tích hợp hệ thống | Máy chủ MCP cho các hệ thống ưu tiên | `[cần điền]` |
| Đào tạo và hỗ trợ | Tài liệu, đào tạo, kênh hỗ trợ, bộ kỹ năng mẫu | `[cần điền]` |

Năng lực mô hình dự kiến là cấu phần chi phí lớn nhất và biến động theo mức sử dụng.
Nền tảng cung cấp ba công cụ kiểm soát: hạn mức tại cổng, mô hình chi phí thấp cho
tác vụ nền (`agent.role_models`) và lựa chọn mô hình theo loại công việc.

## 6. Lộ trình và cổng quyết định

| Giai đoạn | Nội dung | Điều kiện chuyển giai đoạn |
|---|---|---|
| 0. Chuẩn bị | Thẩm định bảo mật, cấu hình sản xuất, lựa chọn đơn vị và công việc thí điểm, đo số nền | An ninh thông tin chấp thuận phạm vi thí điểm |
| 1. Thí điểm | Nhóm giới hạn sử dụng trên công việc đã chọn, đo định kỳ | Đạt ngưỡng chỉ số; không phát sinh sự cố bảo mật nghiêm trọng |
| 2. Mở rộng | Bổ sung đơn vị, bộ kỹ năng chuẩn, tích hợp hệ thống ưu tiên | Chi phí trên mỗi người dùng trong ngưỡng; quy trình hỗ trợ ổn định |
| 3. Nền tảng dùng chung | Đơn vị tự tích hợp và tự đóng gói ứng dụng | Có đơn vị chủ quản và cơ chế phân bổ chi phí |

## 7. Khung đo lường hiệu quả

Chỉ số và ngưỡng được thống nhất trước thí điểm và đo trước, sau.

| Nhóm | Chỉ số | Số nền | Mục tiêu |
|---|---|---|---|
| Mức độ sử dụng | Người dùng hoạt động hằng tuần, số phiên, tỉ lệ quay lại | `[cần điền]` | `[cần điền]` |
| Năng suất | Thời gian hoàn thành các công việc đã chọn | `[cần điền]` | `[cần điền]` |
| Chất lượng | Tỉ lệ kết quả phải thực hiện lại | `[cần điền]` | `[cần điền]` |
| Tự động hóa | Số công việc chạy theo lịch, số kỹ năng dùng chung | `[cần điền]` | `[cần điền]` |
| Chi phí | Chi phí mô hình trên mỗi người dùng | `[cần điền]` | `[cần điền]` |
| An toàn | Số thao tác bị chặn, số sự cố | `[cần điền]` | `[cần điền]` |

## 8. Rủi ro và biện pháp giảm thiểu

| Rủi ro | Biện pháp giảm thiểu |
|---|---|
| Agent thực thi lệnh ngoài ý muốn, đặc biệt trên Windows | Chính sách chặt, phê duyệt bắt buộc với lệnh shell, biện pháp bù do An ninh thông tin quyết định |
| Dữ liệu nhạy cảm được đưa vào hội thoại | Quy định phân loại dữ liệu; chỉ gọi mô hình qua cổng nội bộ |
| Chi phí mô hình vượt dự kiến | Hạn mức tại cổng, mô hình theo vai trò, theo dõi chi phí theo đơn vị |
| Kết quả sai được sử dụng mà không kiểm tra | Người dùng chịu trách nhiệm cuối cùng; đào tạo; phê duyệt đối với thao tác có tác động |
| Khoảng cách với bản gốc tăng dần | Giữ tùy biến trong `editions/` và điểm mở rộng; hợp nhất bản gốc định kỳ |
| Phụ thuộc nhân sự chủ chốt | Bộ tài liệu này; đội nền tảng nhiều hơn một người |
| Mức độ chấp nhận của người dùng thấp | Bắt đầu từ công việc cụ thể, đo được; bộ kỹ năng mẫu theo đơn vị |

## 9. Quyết định đề nghị

1. Phê duyệt giai đoạn 0 và 1 với phạm vi, thời gian, ngân sách: `[cần điền]`.
2. Chỉ định đơn vị chủ quản nền tảng.
3. Chỉ định đầu mối An ninh thông tin thực hiện thẩm định.
4. Thống nhất chỉ số và ngưỡng tại cổng quyết định sau thí điểm.
