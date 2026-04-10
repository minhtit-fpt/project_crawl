# Price Crawler

Công cụ thu thập giá sản phẩm từ các trang web thương mại điện tử. Nhận vào danh sách URL và mã SKU, trích xuất giá bằng **Scrapy + Playwright**, và in kết quả ra terminal dạng bảng có cấu trúc. Mọi lỗi đều được ghi nhận thành `Error` record — crawler không bao giờ crash giữa chừng.

---

## Mục lục

- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt](#cài-đặt)
- [Cấu hình](#cấu-hình)
- [Chạy crawler](#chạy-crawler)
- [Kết quả đầu ra](#kết-quả-đầu-ra)
- [Mã thoát](#mã-thoát)
- [Docker](#docker)
- [Xử lý lỗi](#xử-lý-lỗi)
- [Tests](#tests)
- [FAQ](#faq)

---

## Yêu cầu hệ thống

| Thành phần | Phiên bản |
|------------|-----------|
| Python     | 3.12+     |
| Chromium   | cài qua Playwright |

---

## Cài đặt

```bash
# 1. Clone repo
git clone <repo-url>
cd project_claw

# 2. Cài dependencies
pip install -r requirements.txt

# 3. Cài Chromium (để render trang JavaScript)
playwright install chromium

# 4. Tạo file .env
cp .env.example .env
```

Mở `.env` và điền các giá trị cần thiết:

```bash
# Tạo AES_SECRET_KEY (32 bytes)
python -c "import secrets; print(secrets.token_hex(32))"

# Tạo AES_IV (16 bytes)
python -c "import secrets; print(secrets.token_hex(16))"
```

### Biến môi trường

| Biến             | Bắt buộc | Mô tả |
|------------------|----------|-------|
| `AES_SECRET_KEY` | Có       | Khóa AES-256-CBC — 32 bytes, encode hex |
| `AES_IV`         | Có       | IV cho AES — 16 bytes, encode hex |
| `PROXY_LIST`     | Không    | Danh sách proxy, ngăn cách bằng dấu phẩy |
| `LOG_LEVEL`      | Không    | `DEBUG` / `INFO` / `WARNING` / `ERROR` (mặc định: `INFO`) |
| `DATABASE_URL`   | Không    | URL database (dành cho tính năng lưu trữ sau này) |

---

## Cấu hình

Danh sách trang web được cấu hình tại `scraper/config/sites.yaml`. Thêm trang web mới chỉ cần chỉnh sửa file này — không cần thay đổi code.

```yaml
sites:
  - name: "TenCuaHang"
    base_url: "https://example.com/product/{sku}"   # {sku} được thay tự động
    skus:
      - "SKU-001"
      - "SKU-002"
    selectors:
      price_css: "span.price"                       # CSS selector (ưu tiên)
      price_xpath: "//span[@class='price']"         # XPath fallback
    requires_js: false                              # true = dùng Playwright
    rate_limit_seconds: 2                           # delay giữa các request (giây)
```

**`requires_js: true`** — bật khi trang dùng JavaScript để render giá (React, Vue, Angular...).

---

## Chạy crawler

```bash
# Chạy với cấu hình mặc định
python main.py

# Dùng file cấu hình khác
python main.py --config /path/to/sites.yaml

# Mã hóa cột SKU và Price trong output (AES-256)
python main.py --encrypt

# Ghi đè log level
python main.py --log-level DEBUG

# Kết hợp
python main.py --config sites_prod.yaml --encrypt --log-level WARNING
```

---

## Kết quả đầu ra

```
SKU                   Price            Source                Timestamp (UTC)            Status
--------------------  ---------------  --------------------  -------------------------  ----------
SKU-001               129.99           StaticShop            2026-04-10 08:30:00        OK
SKU-002               —                StaticShop            2026-04-10 08:30:02        Error: HTTP 404
PROD-100              499.00           ReactShop             2026-04-10 08:30:05        OK

Total: 3  |  OK: 2  |  Errors: 1
```

| Cột | Mô tả |
|-----|-------|
| `SKU` | Mã sản phẩm |
| `Price` | Giá trích xuất (float). `—` nếu lỗi |
| `Source` | Tên site trong `sites.yaml` |
| `Timestamp (UTC)` | Thời điểm crawl |
| `Status` | `OK` hoặc `Error: <chi tiết>` |

Khi bật `--encrypt`, cột `SKU` và `Price` hiển thị giá trị mã hóa AES-256-CBC (base64).

---

## Mã thoát

| Code | Ý nghĩa |
|------|---------|
| `0`  | Tất cả request thành công |
| `1`  | Lỗi khởi động (thiếu `.env`, config sai...) |
| `2`  | Crawl xong nhưng có một số SKU lỗi |

---

## Docker

```bash
# Docker Compose (khuyến nghị — tự đọc .env)
docker-compose up --build

# Hoặc thủ công
docker build -t project_claw .
docker run --env-file .env project_claw

# Mount file cấu hình tùy chỉnh
docker run --env-file .env \
  -v /path/to/my_sites.yaml:/app/scraper/config/sites.yaml \
  project_claw
```

> Container chạy dưới user non-root. Chỉ cài Chromium (không có Firefox/WebKit).

---

## Xử lý lỗi

**Retry tự động** với exponential backoff (tối đa 3 lần, từ 1s đến 30s) khi gặp:
- Lỗi kết nối mạng
- HTTP 429, 500, 502, 503
- Playwright timeout

**Không retry** khi gặp HTTP 404 hoặc lỗi parse — các trường hợp này được ghi nhận là `Error` record.

**Proxy:** Round-robin rotation. Proxy lỗi nhiều lần bị đánh dấu dead, tự động fallback về kết nối trực tiếp.

---

## Tests

```bash
# Chạy toàn bộ test suite
pytest

# Kèm báo cáo coverage
pytest --cov=scraper --cov=security --cov-report=term-missing

# Chạy một file test
pytest tests/test_parser.py

# Chạy một test cụ thể
pytest tests/test_parser.py::test_price_with_currency_symbol
```

---

## FAQ

**Lỗi `EnvironmentError: Missing required keys` khi khởi động?**
Kiểm tra file `.env` đã điền đủ `AES_SECRET_KEY` và `AES_IV`.

**Giá không lấy được (`Error: parse failed`)?**
1. Verify selector bằng DevTools của trình duyệt
2. Nếu trang dùng JS để render giá → đặt `requires_js: true`
3. Chạy `--log-level DEBUG` để xem HTML response

**Muốn lưu kết quả ra file?**
```bash
python main.py > results.txt 2>&1
```

**Crawler quá chậm?**
- Giảm `rate_limit_seconds` trong `sites.yaml` (cẩn thận bị block)
- Kiểm tra proxy còn hoạt động không
