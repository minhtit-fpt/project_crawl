# Project Memory: project_claw

## Quy Tắc Cập Nhật Memory (QUAN TRỌNG)

> Claude phải thực hiện tự động — không cần nhắc lại.

| Loại thay đổi | Lưu vào |
|---------------|---------|
| Quyết định kỹ thuật mới | `.claude/memory.md` |
| Thay đổi stack / thư viện | `.claude/memory.md` + `CLAUDE.md` |
| Thay đổi cấu trúc thư mục / module | `.claude/memory.md` + `CLAUDE.md` |
| Thay đổi quy trình (commands, workflow) | `.claude/memory.md` + `CLAUDE.md` |
| Rủi ro mới phát sinh | `.claude/memory.md` |
| Bất kỳ thay đổi nào user đề cập | `.claude/memory.md` (tối thiểu) |

**Thời điểm lưu:** Ngay sau khi user xác nhận hoặc áp dụng thay đổi — không chờ cuối session.

---

## Project Overview
**Type:** Price Crawler (Web Scraper)
**Stack:** Python, Scrapy, Playwright, Docker
**Status:** Greenfield — not yet implemented

---

## Session History

### 2026-04-09
**Task:** Planning phase — Price Crawler System

**Decisions Made:**
- Stack: Python + Scrapy + scrapy-playwright + pycryptodome (AES-256) + python-dotenv + PyYAML
- Architecture: Declarative YAML config per site (CSS/XPath selectors), scheduler generates site×SKU job matrix
- Security: AES-256-CBC for sensitive data, all secrets via .env, never hardcoded
- Error handling: Per-request try-catch, Error record instead of crash
- Output: Formatted table to terminal (Price, SKU, Source, Timestamp, Status)
- Deployment: Docker (multi-stage, non-root user, Chromium only)

**Plan Status:** ✅ Confirmed — implementation in progress

### 2026-04-11 (Phase 6)
**Status:** ✅ COMPLETED — committed to branch `phase/6-docker`, 179 tests pass

**Files created:**
- `Dockerfile` — 3-stage build: builder (pip deps) → browser (Chromium only via playwright install) → runtime (non-root user `crawler`, no build tools)
- `docker-compose.yml` — `env_file:.env`, volume mount `scraper/config/:ro`, memory 1g/cpus 1.0, `restart:on-failure`, `CRAWLER_EXTRA_ARGS` env var cho CLI flags
- `.dockerignore` — loại trừ `.env`, `tests/`, `__pycache__`, `.git`, `.claude`, `docs`

**Key constraints giữ nguyên:** Chromium only, non-root user, secrets từ env_file

### 2026-04-10 (Session 2 — Debug & SKU Resolver)
**Branch:** `phase/5-integration`
**Status:** ✅ COMPLETED — 179 tests pass

**Vấn đề đã fix:**

1. **`scrapy-playwright` chưa cài** — `requirements.txt` pin version cũ không tương thích Python 3.14. Fix: `pip install scrapy-playwright` (cài 0.0.46), update requirements sang `>=` thay vì pin cứng.

2. **`start_requests()` deprecated** (Scrapy 2.13+) — đổi sang `async def start()` trong `spider.py`.

3. **asyncio teardown noise** ("Task was destroyed but it is pending") — suppress bằng `logging.getLogger("asyncio").setLevel(CRITICAL)` trong `main.py` sau `basicConfig`.

4. **Selector sai cho dienmayxanh.com** — `.after-price` không tồn tại. Debug bằng cách dump rendered HTML (Playwright networkidle) ra file → dùng Opus đọc HTML → tìm selector đúng: `.box_servicepack div.active span b` (giá của gói đang active).

5. **Playwright timeout khi wait_for_selector** — bỏ `wait_for_selector`, dùng `wait_for_load_state("networkidle")` thay thế.

**Feature mới: SKU Resolver (resolve mã ngắn → URL)**

Thay vì nhập full slug (`nagakawa-inverter-1-hp-nis-c09r2t28`), user chỉ cần nhập mã ngắn (`NIS-C09R2T28`).

**Files thêm/sửa:**
- `scraper/resolver.py` *(MỚI)* — `resolve_sku_to_url(sku, site)`: mode `direct` (thay {sku} vào base_url) hoặc `search` (GET search_url → parse CSS → lấy href đầu tiên)
- `scraper/config/settings.py` — thêm `SearchSelectors` dataclass, thêm 3 field optional vào `SiteConfig`: `sku_mode`, `search_url`, `search_selectors`
- `scraper/scheduler.py` — `build_jobs()` đổi return type thành `tuple[list[CrawlJob], list[ErrorResult]]`; `run()` merge resolve errors vào results
- `scraper/spider.py` — import `PageMethod`, thêm `wait_for_load_state("networkidle")` cho JS pages, `start()` async
- `main.py` — suppress asyncio logger noise
- `scraper/config/sites.yaml` — dùng mã ngắn + `sku_mode: search`
- `tests/test_resolver.py` *(MỚI)* — 11 tests
- `tests/test_scheduler.py` — fix 5 tests do `build_jobs()` đổi signature
- `tests/test_main.py` — fix mock `load_config` thay vì rely vào `clear=True`
- `requirements.txt` — đổi pin cứng sang `>=` cho scrapy/scrapy-playwright/playwright
- `README.md` *(MỚI)* — hướng dẫn sử dụng đầy đủ
- `docs/usage.md` *(MỚI)* — hướng dẫn chi tiết

**Config dienmayxanh.com đã verified:**
```yaml
search_url: "https://www.dienmayxanh.com/tim-kiem?key={sku}"
search_selectors:
  result_link_css: "a.main-contain"
price_css: ".box_servicepack div.active span b"
```
Kết quả test thực tế: NIS-C18R2T28=10.490.000₫, NIS-C09R2T28=5.490.000₫, MAFA-09CDN8=5.190.000₫

**Known issues tồn tại:**
- `RuntimeError: Event loop is closed` ở cuối run — harmless, từ scrapy-playwright internal thread, không ảnh hưởng kết quả
- Python 3.14 chưa được scrapy-playwright support chính thức nhưng hoạt động được với 0.0.46

---

### 2026-04-10 (Phase 5)
**Status:** ✅ COMPLETED — committed to branch `phase/5-integration`

**Files created:**
- `main.py` — 7-step pipeline: load .env → parse CLI → load sites.yaml → init components → Scheduler.run(spider_runner) → print_results
- Exit codes: `0`=all OK, `1`=crash/config error, `2`=partial errors
- CLI: `--config`, `--encrypt`, `--log-level`
- `spider_runner` closure injects `CrawlerProcess` vào `Scheduler.run()` → Scheduler vẫn testable độc lập
- `tests/test_main.py` — 13 tests: arg parsing, env/config errors, encrypt wiring, exception handling

### 2026-04-10 (Phase 4)
**Status:** ✅ COMPLETED — committed to branch `phase/4-parsing-output`

**Files created:**
- `scraper/parser.py` — `extract_price()` CSS→XPath fallback; `_parse_price_text()` USD/EUR/VND formats; strip non-numeric TRƯỚC khi detect separator
- `scraper/output.py` — `print_results()` aligned table; `format_results()` string; optional `Encryptor`; error rows em-dash + full error
- `tests/test_parser.py` 31 tests + `tests/test_output.py` 20 tests
- **Bug fix:** VND "1.500.000đ" cần strip currency suffix trước khi detect dot-thousands

### 2026-04-09 (Phase 3)
**Status:** ✅ COMPLETED — committed to branch `phase/3-crawling-engine`

**Files created:**
- `scraper/rate_limiter.py` — asyncio `RateLimiter`, per-domain lock, monotonic clock, `reset()` để test
- `scraper/proxy.py` — `ProxyManager`: round-robin, `DEAD_THRESHOLD=3`, `RECOVERY_THRESHOLD=1`, password masking trong log, thread-safe `RLock`
- `scraper/retry.py` — `RetryHandler`: exponential backoff (base=1s, max=30s, jitter ±20%), `RetryableHTTPError` (429/5xx), `NonRetryableError` passthrough, `max_retries=3`
- `scraper/spider.py` — `PriceSpider`: user-agent rotation pool (3 UAs), `playwright` meta cho `requires_js`, `errback` mark proxy dead, `parse()` delegate sang `parser.extract_price()`, mọi lỗi → `ErrorResult`

**Key design decisions:**
- `NonRetryableHTTPStatus` = {404, 410} → ErrorResult ngay, không retry
- `RetryableHTTPStatus` = {429, 500, 502, 503, 504} → retry với backoff
- Rate limiter dùng `asyncio.Lock` (không phải threading) vì Scrapy+Playwright chạy trong asyncio event loop

### 2026-04-09 (Phase 2)
**Status:** ✅ COMPLETED — committed to branch `phase/2-config-scheduling`

**Files created:**
- `scraper/config/sites.yaml` — khai báo sites: name, base_url (với `{sku}` placeholder), skus list, selectors (CSS + XPath fallback), requires_js, rate_limit_seconds
- `scraper/config/settings.py` — `load_sites()` validate YAML → `SiteConfig` frozen dataclass; `SCRAPY_SETTINGS` với Playwright handler, chromium headless, anti-detection args
- `scraper/scheduler.py` — `Scheduler` class: `build_jobs()` expand site×SKU → `CrawlJob` list; `collect_result()` / `run()` interface; `CrawlResult` + `ErrorResult` dataclasses với UTC timestamp; `SpiderRunner` injected để testable

**Key design:** `SpiderRunner` là injected dependency (không hardcode Scrapy trong scheduler) → dễ unit test không cần Scrapy process thật.

### 2026-04-09 (Phase 1)
**Status:** ✅ COMPLETED — committed to branch `phase/1-foundation`

**Files created:**
- `requirements.txt` — pinned deps: scrapy 2.11.2, scrapy-playwright 0.0.40, playwright 1.44.0, pycryptodome 3.20.0, python-dotenv 1.0.1, pyyaml 6.0.2, tabulate 0.9.0, pytest + cov + asyncio
- `.env.example` — template với AES_SECRET_KEY, AES_IV, PROXY_LIST, LOG_LEVEL, DATABASE_URL
- `.gitignore` — .env, __pycache__, .scrapy, venv, IDE, logs
- `security/__init__.py`
- `security/env_loader.py` — load_config() → AppConfig (frozen dataclass), validate hex key/IV lengths, parse PROXY_LIST
- `security/encryption.py` — Encryptor class, AES-256-CBC, PKCS7 padding, base64 output, thread-safe (new cipher per call)

---

## Planned Project Structure

```
project_claw/
├── .env.example
├── .env                         # gitignored
├── .gitignore
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── main.py
├── scraper/
│   ├── scheduler.py
│   ├── spider.py
│   ├── parser.py
│   ├── proxy.py
│   ├── rate_limiter.py
│   ├── retry.py
│   ├── output.py
│   └── config/
│       ├── sites.yaml
│       └── settings.py
├── security/
│   ├── encryption.py
│   └── env_loader.py
└── tests/
    ├── test_parser.py
    ├── test_encryption.py
    ├── test_rate_limiter.py
    ├── test_retry.py
    ├── test_scheduler.py
    └── test_proxy.py
```

---

## Key Technical Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| JS rendering | scrapy-playwright | Maintained library, integrates with Scrapy |
| Encryption | AES-256-CBC via pycryptodome | Standard, battle-tested |
| Config format | YAML (sites.yaml) | Human-readable, easy to add new sites |
| Proxy | Round-robin with dead-proxy tracking | Balance load, handle failures |
| Retry | Exponential backoff, max 3 | Handle transient failures, skip 404s |
| Docker base | python:3.12-slim + Chromium only | Minimize image size |
| SKU resolution | Search page + CSS selector | Tự chủ, không phụ thuộc Google/API bên ngoài |
| Playwright wait | `networkidle` thay vì `wait_for_selector` | Selector cụ thể gây timeout; networkidle ổn định hơn |
| asyncio noise | `logging.getLogger("asyncio").setLevel(CRITICAL)` | scrapy-playwright dùng thread loop riêng, không thể patch exception handler |

---

## Risks Tracked

- **HIGH:** Anti-bot detection (Playwright fingerprinting)
- **HIGH:** Scrapy + Playwright integration complexity
- **MEDIUM:** CSS/XPath selectors break on site redesign
- **MEDIUM:** Docker image size (~500MB with Playwright)
- **MEDIUM:** AES key management in production

---

## Quy Tắc Test (BẮT BUỘC)

> Mỗi phase **phải có test và test phải pass** trước khi bắt đầu phase tiếp theo.

Thứ tự bắt buộc cho mỗi phase:
1. Tạo branch `phase/N-<tên>`
2. Implement các file của phase
3. Viết test cho các file đó trong `tests/`
4. Chạy `pytest tests/` — phải pass hết
5. Commit (code + test cùng nhau hoặc riêng)
6. Mới được bắt đầu phase tiếp theo

---

## Git Workflow (BẮT BUỘC)

> **Trước khi bắt đầu bất kỳ phase nào, PHẢI tạo branch mới.**

Quy ước đặt tên branch:
```
phase/1-foundation
phase/2-config-scheduling
phase/3-crawling-engine
phase/4-parsing-output
phase/5-integration
phase/6-docker
phase/7-tests
```

Quy trình mỗi phase:
1. `git checkout -b phase/N-<tên>` → tạo branch
2. Implement các file của phase đó
3. Commit sau mỗi file hoặc nhóm logic
4. PR / merge khi phase hoàn thành

---

## Next Steps
- ~~Phase 1–5: DONE~~ ✅
- Phase 6: Dockerfile, docker-compose.yml
- Phase 7: Tests (80%+ coverage)
- Cân nhắc: thêm persistent cache cho SKU→URL resolution (tránh gọi search API lặp lại)

---

## Notes
- Project dir: `D:\Project\project_claw`
- All secrets must use `.env` (never hardcode)
- Output format: `Price | SKU | Source | Timestamp | Status`
