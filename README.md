# Price Crawler

Công cụ thu thập giá sản phẩm từ các trang web thương mại điện tử. Hỗ trợ hai chế độ hoạt động:

1. **CLI Mode** — Nhận vào danh sách URL và mã SKU (từ YAML hoặc CMS API), trích xuất giá bằng **Scrapy + Playwright**, in kết quả ra terminal và lưu vào SQLite.
2. **Pull API Server** — Chạy FastAPI server để CMS có thể query giá đã crawl hoặc trigger crawl mới mà không cần vào server.

Mọi lỗi đều được ghi nhận thành `Error` record — crawler không bao giờ crash giữa chừng. Kết quả crawl được lưu vào SQLite để Pull API có thể truy cập.

---

## Mục lục

- [Yêu cầu hệ thống](#yêu-cầu-hệ-thống)
- [Cài đặt](#cài-đặt)
- [Biến môi trường](#biến-môi-trường)
- [Chế độ CLI — Chạy crawl](#chế-độ-cli--chạy-crawl)
- [Chế độ Server — Pull API](#chế-độ-server--pull-api)
- [Cấu hình](#cấu-hình)
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
cd project_crawl

# 2. Cài dependencies
pip install -r requirements.txt

# 3. Cài Chromium (để render trang JavaScript)
playwright install chromium

# 4. Tạo file .env
cp .env.example .env
```

Mở `.env` và điền các giá trị cần thiết dựa trên chế độ sử dụng.

## Biến môi trường

### Chế độ CLI (--source yaml | --source api)

| Biến             | Bắt buộc | Mô tả |
|------------------|----------|-------|
| `PROXY_LIST`     | Không    | Danh sách proxy, ngăn cách bằng dấu phẩy |
| `LOG_LEVEL`      | Không    | `DEBUG` / `INFO` / `WARNING` / `ERROR` (mặc định: `INFO`) |
| `SQLITE_DB_PATH` | Không    | Đường dẫn SQLite database (mặc định: `data/crawl_results.db`) |

### API Source Mode (--source api)

Khi sử dụng `--source api`, các biến này là **bắt buộc**:

| Biến             | Mô tả |
|------------------|-------|
| `CMS_API_URL`    | Endpoint API của CMS để fetch danh sách sản phẩm (ví dụ: `https://cms.example.com/api/products`) |
| `CMS_API_TOKEN`  | Auth token gửi kèm request đến CMS API |

### Pull API Server Mode (--serve)

Khi chạy Pull API server, các biến này điều khiển server:

| Biến             | Mô tả | Mặc định |
|------------------|-------|----------|
| `PULL_API_HOST`  | Host bind cho API server | `0.0.0.0` |
| `PULL_API_PORT`  | Port cho API server | `8080` |
| `PULL_API_TOKEN` | Bearer token yêu cầu cho tất cả requests (để trống để skip auth — dev mode) | (trống) |

---

## Cấu hình

### Mode YAML (--source yaml)

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

### Mode API (--source api)

Crawler fetch danh sách sản phẩm từ CMS API thay vì yaml. CMS API phải trả về danh sách sản phẩm với cấu trúc:

```json
{
  "products": [
    {
      "sku": "SKU-001",
      "url": "https://example.com/product/SKU-001",
      "site_name": "ExampleStore",
      "selectors": {
        "price_css": "span.price",
        "price_xpath": "//span[@class='price']"
      },
      "requires_js": false,
      "rate_limit_seconds": 2
    }
  ]
}
```

---

## Chế độ CLI — Chạy crawl

```bash
# Chạy từ CMS API (mặc định)
python main.py --source api

# Chạy từ YAML
python main.py --source yaml

# Dùng file cấu hình YAML khác
python main.py --source yaml --config /path/to/sites.yaml

# Ghi đè log level
python main.py --log-level DEBUG

# Kết hợp
python main.py --source yaml --config sites_prod.yaml --log-level WARNING
```

---

## Chế độ Server — Pull API

Khởi động Pull API server để CMS có thể query giá hoặc trigger crawl:

```bash
# Khởi động server trên http://0.0.0.0:8080 (default)
python main.py --serve

# Custom host/port (qua .env)
# Chỉnh sửa PULL_API_HOST và PULL_API_PORT trong .env rồi chạy:
python main.py --serve
```

### Pull API Endpoints

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| `GET` | `/health` | Liveness check |
| `GET` | `/prices?sku=SKU001` | Lấy giá đã crawl cho một SKU (mới nhất trước) |
| `GET` | `/prices?sku=SKU001&limit=N` | Giống trên, limit kết quả (max 200) |
| `POST` | `/crawl/trigger` | Trigger crawl mới (async, trả về run_id) |
| `GET` | `/crawl/status/{run_id}` | Kiểm tra trạng thái crawl run |

### Authentication

Nếu `PULL_API_TOKEN` được set trong `.env`, tất cả requests phải kèm header:

```
Authorization: Bearer <PULL_API_TOKEN>
```

Nếu `PULL_API_TOKEN` trống, không cần auth (dev mode — cảnh báo sẽ được ghi log).

### Response Format (GET /prices)

```json
{
  "status": "ok",
  "sku": "SKU001",
  "data": [
    {
      "price": 1299000.0,
      "source": "dienmayxanh.com",
      "crawled_at": "2026-04-13T10:30:15.123456Z",
      "status": "OK",
      "error": null,
      "run_id": "2026-04-13T10:30:00.000001Z"
    }
  ],
  "meta": {
    "total": 1,
    "limit": 50
  }
}
```

---

## Kết quả đầu ra

Sau crawl xong, kết quả được in ra terminal dạng bảng:

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
| `Source` | Tên site |
| `Timestamp (UTC)` | Thời điểm crawl |
| `Status` | `OK` hoặc `Error: <chi tiết>` |

**Lưu trữ:** Tất cả kết quả crawl cũng được lưu vào SQLite database (`SQLITE_DB_PATH`) để Pull API có thể truy cập.

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
- Chế độ API (`--source api`): Kiểm tra `CMS_API_URL` và `CMS_API_TOKEN` trong `.env`
- Chế độ YAML (`--source yaml`): Kiểm tra file `sites.yaml` tồn tại

**Giá không lấy được (`Error: parse failed`)?**
1. Verify selector bằng DevTools của trình duyệt
2. Nếu trang dùng JS để render giá → đặt `requires_js: true` (YAML) hoặc cập nhật CMS API response
3. Chạy `--log-level DEBUG` để xem HTML response

**Muốn lưu kết quả ra file?**
```bash
python main.py --source yaml > results.txt 2>&1
```

**Crawler quá chậm?**
- Giảm `rate_limit_seconds` trong cấu hình (cẩn thận bị block)
- Kiểm tra proxy còn hoạt động không

**Làm sao để Pull API chỉ có thể truy cập từ CMS?**
Đặt giá trị `PULL_API_TOKEN` trong `.env` (ví dụ: `PULL_API_TOKEN=your-secret-token-here`). CMS phải gửi header `Authorization: Bearer your-secret-token-here` với mỗi request.

**Pull API trigger crawl được chạy nền không?**
Có, crawl được chạy trong background thread. POST `/crawl/trigger` trả về `run_id` ngay lập tức. Dùng GET `/crawl/status/{run_id}` để kiểm tra tiến độ.
