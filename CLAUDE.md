# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Price Crawler** — A Python-based scraping tool that accepts a list of target website URLs and product SKUs, extracts prices using Scrapy + Playwright, and outputs structured results to the terminal. Per-request error handling ensures the process never crashes — failed requests produce `Error` records instead.

---

## Phase Workflow (BẮT BUỘC)

Thứ tự bắt buộc cho **mỗi phase**:
1. Tạo branch `phase/N-<tên>`
2. Implement các file
3. Viết test trong `tests/`
4. Chạy `pytest tests/` — phải pass trước khi tiếp tục
5. Commit code + test
6. Mới được bắt đầu phase tiếp theo

---

## Git Workflow

**Bắt buộc:** Tạo branch mới trước khi bắt đầu mỗi phase.

```bash
# Khởi tạo repo (chỉ lần đầu)
git init

# Tạo branch cho từng phase
git checkout -b phase/1-foundation
git checkout -b phase/2-config-scheduling
git checkout -b phase/3-crawling-engine
git checkout -b phase/4-parsing-output
git checkout -b phase/5-integration
git checkout -b phase/6-docker
git checkout -b phase/7-tests
```

Không implement trực tiếp trên `main`. Mỗi phase = 1 branch riêng.

---

## Commands

### Setup
```bash
# Install Python dependencies
pip install -r requirements.txt

# Install Playwright browser (Chromium only)
playwright install chromium

# Copy and fill in environment variables
cp .env.example .env
```

### Run
```bash
# Run the crawler (reads scraper/config/sites.yaml by default)
python main.py

# Custom config path
python main.py --config path/to/sites.yaml

# Encrypt sensitive output fields
python main.py --encrypt

# Set log level
python main.py --log-level DEBUG
```

### Docker
```bash
# Build and run via Docker Compose (uses .env automatically)
docker-compose up --build

# Run standalone container
docker build -t project_claw .
docker run --env-file .env project_claw
```

### Tests
```bash
# Run all tests
pytest

# Run with coverage report
pytest --cov=scraper --cov=security --cov-report=term-missing

# Run a single test file
pytest tests/test_parser.py

# Run a single test by name
pytest tests/test_parser.py::test_price_with_currency_symbol
```

---

## Architecture

### Data Flow

```
main.py
  → security/env_loader.py       # Validate .env at startup — fail fast if missing keys
  → scraper/config/settings.py   # Load sites.yaml, merge into Scrapy settings
  → scraper/scheduler.py         # Expand sites × SKUs into flat CrawlJob list
      → scraper/rate_limiter.py  # Per-domain delay enforcement (asyncio lock)
      → scraper/proxy.py         # Round-robin proxy rotation with dead-proxy tracking
      → scraper/spider.py        # Scrapy spider; uses Playwright for requires_js: true pages
          → scraper/parser.py    # CSS selector → XPath fallback → float extraction
          → scraper/retry.py     # Exponential backoff (max 3); skips 404 / parse errors
      → scraper/output.py        # Print formatted table: Price | SKU | Source | Timestamp | Status
      → security/encryption.py   # Optional AES-256-CBC encrypt before output
```

### Site Configuration (`scraper/config/sites.yaml`)

Each site entry defines its own URL template, SKU list, CSS/XPath selectors, JS requirement, and rate limit:

```yaml
sites:
  - name: "ExampleStore"
    base_url: "https://example.com/product/{sku}"
    skus: ["SKU001", "SKU002"]
    selectors:
      price_css: ".product-price"
      price_xpath: "//span[@class='price']"   # fallback
    requires_js: true
    rate_limit_seconds: 2
```

Adding a new target site only requires editing this YAML — no code changes needed.

### Security Layer (`security/`)

- `env_loader.py` — loads `.env` via `python-dotenv`, validates required keys (`AES_SECRET_KEY`, `AES_IV`, `PROXY_LIST`) at startup, returns a frozen config object
- `encryption.py` — AES-256-CBC via `pycryptodome`; key must be 32 bytes; IV from env; PKCS7 padding; base64 output. Used when `--encrypt` flag is passed or when transmitting data externally

### Crawling Engine (`scraper/`)

- **spider.py** — Scrapy spider. Uses `scrapy-playwright` for JS pages, standard Scrapy for static. Each request is wrapped in try-except; exhausted retries yield `ErrorResult` instead of raising
- **parser.py** — Stateless price extractor. Tries CSS, falls back to XPath. Strips currency symbols and thousands separators, converts to `float`. Returns `None` on any parse failure
- **retry.py** — Decorator with exponential backoff (`base=1s`, `max=30s`, `max_retries=3`). Retries on: connection errors, HTTP 429/500/502/503, Playwright timeout. Does **not** retry HTTP 404 (legitimate "not found")
- **proxy.py** — Reads proxy list from `PROXY_LIST` env var (comma-separated). Round-robin rotation; marks proxy dead after N consecutive failures; falls back to direct connection if all proxies dead
- **rate_limiter.py** — Per-domain token bucket. Thread-safe via asyncio lock. Limit configured per-site in YAML

---

## Environment Variables (`.env`)

| Variable | Required | Description |
|----------|----------|-------------|
| `AES_SECRET_KEY` | Yes | 32-byte key for AES-256-CBC (hex or base64) |
| `AES_IV` | Yes | 16-byte IV for AES-256-CBC |
| `PROXY_LIST` | No | Comma-separated proxy URLs |
| `LOG_LEVEL` | No | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `DATABASE_URL` | No | Optional — for future persistence layer |

---

## Key Constraints

- **Python 3.12** target
- **Chromium only** in Docker — do not install Firefox/WebKit (image size)
- `parser.py` must remain stateless — no side effects, testable in isolation
- Retry logic must **not** retry on HTTP 404 or parse failures — these are valid `Error` records
- AES key validation (32 bytes) happens in `env_loader.py` at startup, not at encrypt time
- Docker container runs as **non-root user**
