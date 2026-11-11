# Sao Khuê: định vị so với Kiro, Microsoft Copilot, Copilot Cowork và Amazon Quick

Tài liệu dành cho Ban Điều hành và người trình bày. VPBank đã đầu tư Kiro và
Microsoft Copilot, có thể kích hoạt Copilot Cowork, và Amazon Quick là một lựa chọn
trên thị trường. Tài liệu xác định vai trò của Sao Khuê trong danh mục đó.

Thông tin về sản phẩm của nhà cung cấp lấy từ nguồn công khai tra cứu tháng 11/2026,
liệt kê tại mục 9. Giá và tính năng cần được đối chiếu với hợp đồng của VPBank trước
khi dùng làm căn cứ quyết định.

## 1. Kết luận

Sao Khuê bổ sung cho các sản phẩm VPBank đã đầu tư. Copilot Cowork và Amazon Quick là
agent chạy trên đám mây của nhà cung cấp, xử lý dữ liệu đã nằm trên đám mây đó và
tính phí theo mức sử dụng. Sao Khuê chạy trên máy trạm và trong mạng nội bộ của Ngân
hàng, gọi mô hình qua cổng AI LLM nội bộ và thuộc sở hữu của VPBank.

Khuyến nghị về phân vai:

| Loại công việc | Công cụ |
|---|---|
| Công việc trong Microsoft 365 | Microsoft Copilot và Copilot Cowork |
| Lập trình trong IDE | Kiro |
| Công việc trên máy trạm, hệ thống nội bộ và dữ liệu phải xử lý qua cổng nội bộ | Sao Khuê |

## 2. Hồ sơ các sản phẩm của nhà cung cấp

### 2.1. Microsoft 365 Copilot và Copilot Cowork

| Tiêu chí | Thông tin |
|---|---|
| Copilot | Trợ lý trong Word, Excel, Outlook, Teams; giá niêm yết 30 USD mỗi người dùng mỗi tháng cho doanh nghiệp lớn |
| Copilot Cowork | Chế độ agent của Copilot cho công việc dài, nhiều bước, phát triển trên cùng công nghệ với Claude Cowork của Anthropic; tiếp tục chạy khi máy người dùng tắt |
| Nơi thực thi | Đám mây Microsoft; thao tác trên tài liệu trong tenant Microsoft 365, không làm việc trực tiếp với tệp trên máy người dùng |
| Cơ chế tính phí | Copilot Credit cộng thêm vào giấy phép Copilot; 0,01 USD mỗi credit theo hình thức trả theo mức dùng, hoặc cam kết trước khối lượng để nhận chiết khấu |
| Quản trị | Tắt theo mặc định; quản trị viên quyết định đối tượng sử dụng và đặt hạn mức chi ở cấp tenant, nhóm, người dùng |
| Mô hình | Anthropic cho mọi khách hàng; OpenAI và mô hình của Microsoft cho khách hàng thuộc chương trình Frontier |
| Xử lý dữ liệu | Theo tài liệu Microsoft, mô hình Anthropic trong Copilot nằm ngoài cam kết EU Data Boundary và cam kết xử lý trong nước; Anthropic là nhà xử lý phụ cần quản trị viên kích hoạt |

### 2.2. Amazon Quick

| Tiêu chí | Thông tin |
|---|---|
| Bản chất | Không gian làm việc agent của AWS, phát triển từ QuickSight: chat agent, nghiên cứu, BI, Flows, Quick Automate (gói Enterprise) |
| Giá gói tổ chức | Essentials 3 USD, Professional 20 USD, Enterprise 40 USD mỗi người dùng mỗi tháng; cộng 250 USD mỗi tài khoản mỗi tháng |
| Giờ agent | 1, 8 và 18 giờ mỗi người dùng mỗi tháng theo gói; vượt mức 3 USD mỗi giờ |
| Nơi thực thi | Đám mây AWS; đã có tại region Singapore, dữ liệu lưu và xử lý tại region đó |
| Mở rộng | Hơn 50 connector; MCP là loại connector tùy biến, kết nối được qua VPC; có ứng dụng desktop |

### 2.3. Kiro

Công cụ agent cho lập trình (IDE và CLI). Gói doanh nghiệp tính theo credit, quản lý
qua IAM Identity Center, có registry MCP để quản trị viên giới hạn máy chủ MCP.

## 3. So sánh

| Tiêu chí | Kiro | Copilot và Cowork | Amazon Quick | Sao Khuê |
|---|---|---|---|---|
| Phạm vi | Lập trình | Công việc văn phòng trong Microsoft 365 | Phân tích, nghiên cứu, tự động hóa trên dữ liệu AWS và SaaS | Máy trạm và hệ thống nội bộ |
| Nơi agent thực thi | Máy người dùng, mô hình tại AWS | Đám mây Microsoft | Đám mây AWS | Máy trạm hoặc máy chủ của VPBank |
| Tệp và lệnh trên máy trạm | Có, trong dự án mã | Không | Qua ứng dụng desktop | Có |
| Hệ thống nội bộ | Qua MCP | Qua connector của Microsoft | Qua connector, MCP qua VPC | Qua MCP trong mạng nội bộ |
| Mô hình AI | Nhà cung cấp lựa chọn | Anthropic, OpenAI, Microsoft | AWS lựa chọn | Mọi mô hình sau cổng AI LLM nội bộ |
| Luồng dữ liệu | Hạ tầng AWS | Hạ tầng Microsoft và nhà xử lý phụ | Region AWS đã chọn | Cổng AI LLM do VPBank cấu hình |
| Chạy không cần người trực | Hạn chế | Có | Có | Có |
| Chính sách và kiểm toán | Của nhà cung cấp | Của Microsoft | Của AWS | Của VPBank, áp dụng chung cho mọi bộ máy agent |
| Cơ cấu chi phí | Theo người dùng và credit | Theo người dùng, cộng credit theo mức dùng | Theo người dùng, cộng giờ agent | Không phí bản quyền; chi phí mô hình và đội duy trì |
| Trách nhiệm hỗ trợ | Nhà cung cấp | Microsoft | AWS | VPBank |

## 4. Năm điểm khác biệt của Sao Khuê

| Khác biệt | Diễn giải |
|---|---|
| Thực thi tại nơi phát sinh công việc kỹ thuật và vận hành | Kho mã, log, script, công cụ dòng lệnh và hệ thống chỉ truy cập được từ mạng nội bộ nằm ngoài phạm vi của agent chạy trên đám mây nhà cung cấp |
| VPBank quyết định luồng dữ liệu | Mọi lệnh gọi mô hình đi qua cổng AI LLM nội bộ. Việc xác định loại dữ liệu bắt buộc xử lý qua đường nội bộ thuộc thẩm quyền của Pháp chế và An ninh thông tin |
| Một chính sách cho mọi bộ máy agent | Sao Khuê đặt cổng kiểm soát quyền và nhật ký kiểm toán của VPBank trước mọi bộ máy agent, kể cả Kiro CLI |
| Chi phí đo tại một điểm | Cowork tính credit, Quick tính giờ agent, Kiro tính credit. Sao Khuê sử dụng năng lực mô hình sẵn có tại cổng nội bộ |
| Quyền sở hữu tri thức và quyền lựa chọn | Kỹ năng, bộ nhớ và lịch sử ở định dạng mở trên hạ tầng VPBank; 11 bộ máy agent chọn được qua cấu hình |

## 5. Quan hệ với Kiro

Nền tảng gốc của Sao Khuê được xây dựng quanh Kiro CLI. Khi chạy dưới Sao Khuê, Kiro
CLI được bổ sung bộ nhớ qua các phiên, lập lịch, tác vụ dài có điểm khôi phục, đội
agent chạy song song và cổng kiểm soát của VPBank. Bản Sao Khuê bật sẵn chế độ
registry MCP doanh nghiệp để tương thích với tài khoản Kiro doanh nghiệp. Sao Khuê do
đó gia tăng giá trị khoản đầu tư Kiro hiện có.

## 6. Phạm vi Sao Khuê không hướng tới

- Công việc trong Microsoft 365: Copilot và Cowork tích hợp sâu hơn và được Microsoft
  hỗ trợ.
- BI và phân tích trên dữ liệu AWS: thuộc phạm vi của Amazon Quick nếu VPBank lựa
  chọn sản phẩm này.
- Người dùng nghiệp vụ chỉ làm việc với tài liệu văn phòng: kích hoạt Cowork đơn giản
  hơn triển khai thêm một công cụ.

## 7. Phân vai chi tiết

| Công việc | Công cụ phù hợp |
|---|---|
| Email, họp, tài liệu, bảng tính trong Microsoft 365 | Copilot |
| Công việc văn phòng dài, nhiều bước trên dữ liệu Microsoft 365 | Copilot Cowork |
| Viết và sửa mã trong IDE | Kiro |
| Công việc trên kho mã, log, script, công cụ dòng lệnh | Sao Khuê |
| Công việc cần gọi hệ thống chỉ có trong mạng nội bộ | Sao Khuê |
| Giám sát và kiểm tra định kỳ trên hạ tầng nội bộ | Sao Khuê |
| Công việc với dữ liệu chỉ được xử lý qua cổng nội bộ | Sao Khuê |
| Hạng mục lớn cần nhiều agent chạy song song trên mã nguồn | Sao Khuê |

## 8. Điều kiện để Sao Khuê có ý nghĩa

Sao Khuê tạo giá trị khi VPBank có ít nhất một trong các nhu cầu: agent làm việc trên
máy trạm hoặc hệ thống nội bộ; tồn tại loại dữ liệu phải xử lý qua đường nội bộ; yêu
cầu một chính sách và một nhật ký cho mọi agent; yêu cầu độc lập với nhà cung cấp.

Trường hợp mọi công việc giao cho agent đều nằm trong Microsoft 365 và mọi dữ liệu
liên quan đều được phép xử lý trên hạ tầng nhà cung cấp, Copilot, Cowork và Kiro đáp
ứng đủ nhu cầu.

Sao Khuê đi kèm chi phí riêng: đội nền tảng nội bộ, trách nhiệm hỗ trợ thuộc VPBank
và các giới hạn nêu tại [bảo mật và quản trị](04-bao-mat-va-quan-tri.md), mục 5.

## 9. Nguồn

- Computerworld, "Microsoft launches Copilot Cowork with usage-based pricing":
  <https://www.computerworld.com/article/4186190/microsoft-launches-copilot-cowork-with-usage-based-pricing.html>
- Microsoft Learn, kết nối nhà xử lý phụ AI cho Microsoft 365 Copilot:
  <https://learn.microsoft.com/copilot/microsoft-365/connect-to-ai-subprocessor>
- Amazon Quick, bảng giá: <https://aws.amazon.com/quick/pricing/>
- AWS, Amazon Quick tại region Singapore:
  <https://aws.amazon.com/about-aws/whats-new/2026/10/amazon-quick-now-available-in-the-aws-singapore-region/>
- AWS, Amazon Quick hỗ trợ kết nối VPC cho MCP:
  <https://aws.amazon.com/about-aws/whats-new/2026/06/amazon-quick-vpc-mcp/>
- Kiro, registry MCP: <https://kiro.dev/docs/mcp/registry/>
- Kiro, gộp credit doanh nghiệp: <https://kiro.dev/docs/enterprise/credit-pooling/>
