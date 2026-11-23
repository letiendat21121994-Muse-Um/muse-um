# Meta Ads Alerts — Package A

Dashboard local dành cho Tiến: đọc số liệu và cảnh báo bằng tiếng Việt, không pause, đổi budget hay publish quảng cáo. Đây là app độc lập trong repo, không cần chạy Relay hoặc Panel.

## Thử demo ngay

Chạy tại thư mục gốc của repo với Python 3.12 và cơ sở dữ liệu múi giờ IANA của hệ điều hành; không cài package Python:

```sh
python3.12 -m meta_ads_alerts
```

Mở **http://127.0.0.1:8765**. Dữ liệu của cửa hàng chăm sóc tóc tại US là giả lập; chế độ mặc định không cần token, không kết nối Meta. Chọn **Chi tiêu**, **Delivery**, **Tracking** hoặc **Hiệu suất**, rồi chọn một dòng để xem điều gì thay đổi và việc nên kiểm tra trong Ads Manager hoặc Events Manager. Dừng server bằng Ctrl+C.

- [Ảnh 1 — Tổng quan và cảnh báo đang mở](docs/screenshots/01-overview.png)
- [Ảnh 2 — Lọc Tracking và xem gợi ý kiểm tra](docs/screenshots/02-tracking.png)

In cảnh báo một lần ra terminal, không mở server:

```sh
python3.12 -m meta_ads_alerts check
```

## Cấu hình và alert log

Sao chép [config.example.json](config.example.json) để tùy chỉnh, rồi truyền `--config /đường/dẫn/config.json` cho lệnh `serve` hoặc `check`. Mặc định: poll mỗi 3.600 giây, baseline là 7 ngày đã kết thúc, chỉ xem cảnh báo dựa trên dữ liệu đủ mẫu. Các ngưỡng tăng/giảm là tỷ lệ (ví dụ `0.40` là 40%), spend dùng đơn vị tiền tệ của ad account. Chu kỳ poll không phải độ trễ attribution. Chọn cổng local khác với `--port` nếu cần.

`serve` lưu cảnh báo mới theo từng dòng JSON tại `~/.local/state/meta-ads-alerts/alerts.jsonl` (có thể đổi với `--log-path`). Một cảnh báo không bị ghi lại khi vẫn đang mở trong cùng tiến trình; nó được ghi lại khi hết rồi xuất hiện lại, hoặc khi server khởi động lại. `check` chỉ in ra terminal. Không ghi token hay nội dung review feedback vào log; file có thể có tên campaign/ad nên giữ file ở local và xóa/rotate khi cần. Không có email, Slack hay WhatsApp trong bản này.

## Kết nối Meta sau này bằng quyền chỉ đọc

Chỉ dùng khi **đã có** token `ads_read` và được cấp quyền đọc ad account. App không tạo token, tài khoản hay hồ sơ App Review. Cấp các biến môi trường sau từ phương thức quản lý secret của bạn; không dán token vào command line, config, screenshot hoặc Git.

| Biến | Ý nghĩa |
|---|---|
| `META_ADS_TOKEN` | Access token có quyền `ads_read` và quyền truy cập ad account. |
| `META_AD_ACCOUNT_ID` | ID số của ad account, có thể có tiền tố `act_`. |
| `META_GRAPH_VERSION` | Tùy chọn; mặc định `v27.0`. Chỉ đổi sang phiên bản đã đối chiếu tài liệu. |

```sh
python3.12 -m meta_ads_alerts serve --source meta
# hoặc kiểm tra một lần: python3.12 -m meta_ads_alerts check --source meta
```

App chỉ gửi **GET** đến Graph API qua HTTPS. Không xin `ads_management`, không dùng webhooks, và không gửi event qua Pixel/Conversions API. [Tài liệu quyền Marketing API](https://developers.facebook.com/docs/marketing-api/get-started/authorization/) phân biệt app đọc tài khoản của mình với app đọc cho người ngoài; Advanced Access và App Review có thể cần thiết tùy người dùng/app. Dashboard chỉ bind `127.0.0.1`, không có login; không đưa ra Internet hoặc dùng reverse proxy để mở cho người khác.

## Cách đọc tín hiệu và giới hạn

- **Hiệu suất:** hôm nay theo múi giờ ad account so với baseline ngày hoàn chỉnh. CPA = spend / Website Purchase; ROAS = giá trị Website Purchase / spend. CTR, CPC và conversion rate dùng link click; CPM dùng impressions. Frequency cao là dấu hiệu cần kiểm tra, không chứng minh creative fatigue.
- **Chi tiêu:** app so spend với mức tham chiếu tuyến tính theo thời gian trong ngày, không dự đoán cách Meta phân bổ. Chỉ kiểm tra daily budget của campaign hoặc ad set phù hợp, hiện hỗ trợ USD và VND. Không đánh giá lifetime budget hoặc lịch mới bắt đầu/kết thúc giữa ngày nếu có thông tin lịch. Xem [đơn vị budget](https://developers.facebook.com/docs/marketing-api/currencies/).
- **Delivery:** kiểm tra `effective_status` (gồm disapproval và delivery issue) và Learning Limited nếu Meta trả `learning_stage_info.status=FAIL`. Nếu Meta từ chối trường tùy chọn, dashboard chỉ rõ giới hạn; xem lý do chi tiết trong Ads Manager. Xem tài liệu [Ad](https://developers.facebook.com/docs/marketing-api/reference/adgroup/) và [Learning Stage Info](https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-learning-stage-info/).
- **Tracking:** lấy Website Purchase/Checkout được quy gán qua [Ads Insights](https://developers.facebook.com/docs/marketing-api/insights/) (conversion reporting, cửa sổ yêu cầu 7-day click/1-day view). Tín hiệu giảm hoặc về 0 không chứng minh Pixel/CAPI lỗi: đối chiếu số đơn của cửa hàng và [Events Manager / Test Events](https://developers.facebook.com/docs/marketing-api/conversions-api/verifying-setup/) bằng tay. Không đọc dữ liệu khách hàng hoặc nhập dữ liệu cửa hàng trong bản này; không sao chép diagnostics của Events Manager.
- Nếu poll lỗi, app hiển thị lý do và đánh dấu dữ liệu cũ thay vì coi lỗi là “không có cảnh báo”. Dữ liệu Meta có thể đến trễ; Meta cung cấp field/quyền tùy tài khoản và phiên bản API. Bản này chỉ kiểm thử với demo và HTTP giả lập, chưa kết nối tài khoản Meta thật.

## Kiểm thử

```sh
python3.12 -m unittest discover -s meta_ads_alerts/tests -v
```
