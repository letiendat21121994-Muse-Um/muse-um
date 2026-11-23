# Meta Video Ads · Studio demo

App Next.js độc lập của Task 04; không cần Relay, Panel, daemon hay PR khác. UI tiếng Việt, timezone `Asia/Ho_Chi_Minh`, tiền tệ VND. **Xuất bản và số liệu trong web app luôn là demo:** không gửi yêu cầu tới Meta, không xét duyệt hay phân phối quảng cáo, không chi tiền.

## Chạy local

Cần **Node.js 24+**, npm, `ffmpeg` và `ffprobe` trong PATH. Không cần tài khoản, token, database server hay AI key để chạy demo.

```sh
cd apps/meta-video-ads
npm ci
npm run dev
```

Mở **http://127.0.0.1:3000**. Sao chép `.env.example` thành `.env.local` nếu muốn chỉnh giới hạn hoặc cổng; các mặc định đã đủ để chạy. Lệnh chạy web app và worker; dừng cả hai bằng Ctrl+C.

1. **Bắt đầu phiên demo** → **Tải video** → kéo thả nhiều tệp, hoặc **Thêm 3 video mẫu**. Video mẫu là clip minh hoạ do app tạo và có landing page demo. Nếu tải video riêng, đặt URL đích ở bước quảng cáo hoặc chỉnh từng video.
2. **Duyệt & chỉnh sửa** → mở dòng video để so sánh copy, chỉnh và lưu hoặc chọn thêm phiên bản. Lô nháp lưu trên máy chủ; mở lại bằng cùng browser.
3. **Chiến dịch** → chọn một nhóm chung hoặc nhiều nhóm. Khi dùng nhiều nhóm, gán nhóm cho từng video ở màn hình duyệt. Chọn budget ở cấp chiến dịch hoặc nhóm; xem dự toán, lịch và mục bị loại ở **Xuất bản**.
4. Chọn **Mô phỏng một lỗi để thử phục hồi** → xác nhận **Xuất bản quảng cáo đã chọn (demo)**. Các mục còn lại tiếp tục; bấm **Thử lại quảng cáo lỗi** để hoàn thành từ bước còn thiếu.
5. **Tổng quan** → lọc theo lô/ngày/nhóm/trạng thái; nhấn tên chiến dịch → nhóm → quảng cáo, hoặc xem theo video và copy. Có chart, media/copy gốc, CSV và làm mới snapshot. Snapshot minh hoạ bảy ngày, độc lập với ngân sách hoặc lịch nhập; không phải dự báo hay chi phí chạy thực tế.

## Kiến trúc và vận hành

- **Web:** TypeScript, Next.js và Tailwind CSS. API chỉ đọc/ghi tài nguyên của phiên hiện tại. Phiên demo dùng cookie HttpOnly/SameSite và token ngẫu nhiên, lưu hash ở server, hết hạn sau 30 ngày; mất cookie không thể phục hồi phiên hoặc dùng chung giữa thiết bị. Không đăng ký tài khoản hay nhập PII.
- **Lưu trữ:** SQLite/WAL với migration, local volume và worker riêng thay cho PostgreSQL, object storage và hosted queue; phù hợp local/single instance, không cần tài khoản ngoài. Media riêng theo ID ngẫu nhiên, kiểm tra quyền kể cả HTTP range. SQLite, media và queue nằm dưới `DATA_DIR/demo`; video mẫu là tài sản công khai, không phải upload của user.
- **Worker:** một job đang chạy mỗi thời điểm, lease lưu bền, retry giới hạn với lỗi provider tạm thời. Publish kiểm tra fingerprint của bản review, khóa bản nháp, tạo đúng một campaign và dùng lại media/creative đã lưu khi retry. Tệp hỏng hoặc ad lỗi không ngăn các mục khác.
- **WebM:** nếu container không khai báo thời lượng, giới hạn được tính trên track hình đầu tiên, không tính audio preroll.
- **Reporting:** chỉ cộng metric cộng được, tính lại tỷ lệ trên tổng, không cộng reach qua ad/ngày; thiếu metric hiện “—”. Click = link click; kết quả mua hàng dùng Purchase. Các dòng khác currency bị từ chối khi tổng hợp; demo chỉ dùng VND.
- **Triển khai nội bộ:** `npm ci && npm run build`, đặt `DATA_DIR` tuyệt đối trên volume bền vững (chỉ service account được đọc), rồi `npm start`. Một instance web/worker; chạy bằng user không có quyền đặc biệt. Sau reverse proxy HTTPS, đặt `SECURE_COOKIE=true`, giới hạn request body/timeouts và concurrency tương ứng, dùng xác thực/allowlist tại proxy, cập nhật FFmpeg và đặt resource limits cho worker. Không public Internet bản MVP; chưa có login liên thiết bị, quota tổng theo workspace, chính sách dọn dữ liệu hết hạn, AV scan, đa instance hoặc HA. Backup SQLite nhất quán cùng media; dùng backup API hoặc dừng app trước khi copy. Không đưa upload, database, token hoặc key vào git.

## Gemini (tuỳ chọn, không cần cho demo)

Chỉ bật khi đã có key được phép dùng và chấp nhận điều khoản/chi phí của provider. Inject `GEMINI_API_KEY` vào server; `GEMINI_MODEL` mặc định `gemini-2.5-flash`. Không dùng biến `NEXT_PUBLIC_*`. Khởi động lại app, chọn ô gửi video tới Gemini trước khi tải lên. Gemini nhận tệp tối đa 12 MiB với thời lượng container xác định không quá 60 giây, cùng bối cảnh đã lưu; server xác minh JSON theo schema. WebM thiếu metadata thời lượng chỉ dùng được ở chế độ mô phỏng. Copy mới dùng analysis lưu sẵn, không gửi lại video. Video mẫu luôn dùng fixture; upload ở chế độ mô phỏng không được nhận là quan sát thật. Kiểm tra tuyên bố trước khi dùng; adapter chưa gọi trực tiếp vì chưa dùng credential.

Tài liệu: [Gemini video](https://ai.google.dev/gemini-api/docs/video-understanding) · [Structured output](https://ai.google.dev/gemini-api/docs/structured-output).

## Meta — điều kiện cho giai đoạn sau

**Không kết nối tài khoản thật trong Task 04. Không chạy các lệnh sau với tài khoản của chủ repo hoặc chỉ để kiểm tra demo.** Web app không có live publish, OAuth hoặc live reporting, và không đọc kho live. Có CLI kiểm tra/nhập campaign **chỉ đọc** dành cho môi trường sau này đã được phê duyệt; chưa được xác minh với tài khoản thật. CLI dùng Graph API v27.0 ([changelog](https://developers.facebook.com/docs/graph-api/changelog/)) và chỉ gọi GET tới [Ad Account](https://developers.facebook.com/docs/marketing-api/reference/ad-account/) và [Campaigns](https://developers.facebook.com/docs/marketing-api/reference/ad-account/campaigns/).

- Cần app Meta được phép dùng Marketing API và quyền trên ad account. Cấp token server có `ads_read`, ad account ID, và key ngẫu nhiên 32 byte (64 ký tự hex) để mã hóa AES-256-GCM. **Không xin `ads_management` cho CLI**. Dùng secret manager đặt `META_ACCESS_TOKEN`, `META_VAULT_KEY` và `META_AD_ACCOUNT_ID`, không dán secret vào terminal, URL, browser hay config được commit. CLI đọc biến môi trường của process.
- Khi đã được cho phép: `npm run meta:readonly -- store-token` lưu token mà không kết nối. Bỏ raw token khỏi môi trường; CLI dùng key và bản mã. `npm run meta:readonly -- inspect --authorized-live-read` đọc currency/timezone/status; `npm run meta:readonly -- import --authorized-live-read` lưu campaign metadata với source trong `DATA_DIR/meta-readonly/campaigns.json`, không nhập vào dashboard demo. `npm run meta:readonly -- disconnect` xóa token và dữ liệu local; thu hồi token ở Meta riêng khi cần. Nếu token hết hạn/bị thu hồi, ngắt kết nối và chỉ lưu lại token mới đã cấp quyền.
- Nếu triển khai live: dùng OAuth server-side có state và token vault, xác nhận [quyền và cấp truy cập](https://developers.facebook.com/docs/marketing-api/get-started/authorization/) và [App Review](https://developers.facebook.com/docs/app-review/). `ads_management` cần cho viết, `ads_read` cho báo cáo/import. Advanced Access, App Review và Business Verification có thể cần với tài khoản người khác. Xác minh quyền ad account/Page/Instagram, Purchase dataset và tín hiệu Pixel/CAPI, special ad categories và tổ hợp mục tiêu/tối ưu/vị trí theo account.
- Xem [Campaign](https://developers.facebook.com/docs/marketing-api/reference/ad-campaign-group/), [Ad Set](https://developers.facebook.com/docs/marketing-api/reference/ad-campaign/), [đơn vị currency](https://developers.facebook.com/docs/marketing-api/currencies/) và [Insights](https://developers.facebook.com/docs/marketing-api/insights/). Với live, đọc timezone/currency thực, kiểm tra mức tối thiểu và đơn vị ngân sách; tách API submission khỏi review/delivery; dùng reach từ API cho đúng reporting scope; đối chiếu attribution và reconciliation khi kết quả ghi chưa rõ. Không tự thay budget hoặc campaign. Không có giao dịch live hay yêu cầu App Review nào trong PR này.

## Kiểm thử

```sh
npm run typecheck
npm test
npm run build
npm run test:api
```

`npm test` kiểm tra isolation, mapping, validation và budget, idempotency, phục hồi sau lỗi và mở lại database, aggregation và Meta client dùng HTTP giả lập. `test:api` dùng production build, local worker, tệp video hợp lệ/hỏng và hai phiên độc lập, xác minh upload/range, chỉnh và tạo lại copy, publish trùng/partial retry, dashboard data và CSV. Test dùng data dir tạm và không gọi Meta hay Gemini.
