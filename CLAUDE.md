# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Price Crawler** — A Python-based web scraping tool with two operational modes:

1. **CLI Mode**: Accepts a list of target website URLs and product SKUs (from YAML file or CMS API), extracts prices using Scrapy + Playwright, outputs structured results to the terminal, and persists to MySQL.

2. **Pull API Server Mode**: Runs a FastAPI server that allows a CMS to query previously crawled prices by SKU or trigger new crawls asynchronously.

Per-request error handling ensures the process never crashes — failed requests produce `Error` records instead.

---

## Git Workflow

**Bắt buộc:** Tạo branch mới trước khi bắt đầu development (không implement trực tiếp trên `main`).

```bash
# Tạo branch cho feature
git checkout -b feat/<feature-name>

# Tạo branch cho bugfix
git checkout -b fix/<bug-name>
```

Commit message format:
```
<type>: <description>

<optional body>
```

Types: feat, fix, refactor, docs, test, chore, perf, ci

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

# Start MySQL (Docker Compose)
docker-compose up -d mysql
# Wait for MySQL to be healthy (~15-30s on first boot)
docker-compose ps mysql
```

### Run — CLI Mode (Crawl)

#### From YAML Configuration
```bash
# Run with YAML config (scraper/config/sites.yaml by default)
python main.py --source yaml

# Custom config path
python main.py --source yaml --config path/to/sites.yaml

# Set log level
python main.py --source yaml --log-level DEBUG
```

#### From CMS API
```bash
# Run with CMS API source (default in --source flag)
python main.py --source api

# Requires CMS_API_URL and CMS_API_TOKEN in .env
python main.py --source api --log-level DEBUG
```

### Run — Server Mode (Pull API)

```bash
# Start Pull API server
# Binds to PULL_API_HOST:PULL_API_PORT (default: 0.0.0.0:8080 from .env)
python main.py --serve

# Server exposes:
# - GET  /prices?sku=X           → Query crawled prices for SKU X
# - POST /crawl/trigger          → Trigger new crawl (async)
# - GET  /crawl/status/{run_id}  → Check crawl progress
```

### Docker
```bash
# Build and run via Docker Compose (starts MySQL + crawler)
docker-compose up --build

# Run standalone container in CLI mode
docker build -t project_crawl .
docker run --env-file .env project_crawl

# Run in server mode
docker run --env-file .env -p 8080:8080 project_crawl python main.py --serve
```

### Tests
```bash
# Run all tests (requires Docker for testcontainers to spin up MySQL automatically)
pytest

# Run with coverage report
pytest --cov=scraper --cov=security --cov-report=term-missing

# Run only unit tests (no Docker/MySQL required)
pytest -m "not integration"

# Run only integration tests
pytest -m integration

# Run a single test file
pytest tests/test_parser.py

# Run a single test by name
pytest tests/test_parser.py::test_price_with_currency_symbol
```

---

## Architecture

### Data Flow

#### CLI Mode (Crawl)

```
main.py --source [yaml|api]
  → security/env_loader.py           # Validate .env at startup
  → scraper/config/settings.py       # Load sites.yaml (YAML mode)
  → scraper/config/api_source.py     # Fetch from CMS API (API mode)
  → scraper/scheduler.py             # Expand sites × SKUs into CrawlJob list
      → scraper/rate_limiter.py      # Per-domain rate limiting
      → scraper/proxy.py             # Round-robin proxy rotation
      → scraper/spider.py            # Scrapy spider + Playwright for JS pages
          → scraper/parser.py        # CSS/XPath → price float
          → scraper/retry.py         # Exponential backoff
      → scraper/output.py            # Print formatted table to terminal
      → scraper/storage/database.py  # Persist results to MySQL
```

#### Server Mode (Pull API)

```
main.py --serve
  → security/env_loader.py
  → scraper/storage/database.py      # Init MySQL schema
  → scraper/api_server.py            # FastAPI server
      ├─ GET /prices?sku=X           → Query MySQL for prices
      ├─ POST /crawl/trigger         → Spawn background crawl thread
      └─ GET /crawl/status/{run_id}  → Check background thread status
```

### Configuration Sources

#### YAML Mode (`scraper/config/sites.yaml`)

Each site entry defines URL template, SKU list, CSS/XPath selectors, JS requirement, and rate limit:

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

#### API Mode (CMS Endpoint)

Crawler fetches products from `CMS_API_URL` using `CMS_API_TOKEN` auth.

CMS API response format:

```json
{
  "products": [
    {
      "sku": "SKU001",
      "url": "https://example.com/product/SKU001",
      "site_name": "ExampleStore",
      "selectors": {
        "price_css": ".product-price",
        "price_xpath": "//span[@class='price']"
      },
      "requires_js": true,
      "rate_limit_seconds": 2
    }
  ]
}
```

### Pull API Server (`scraper/api_server.py`)

FastAPI server exposing crawl results and crawl trigger via REST API.

**Endpoints:**
- `GET /health` — Liveness check
- `GET /prices?sku=X` — List crawled prices for SKU X (newest first)
- `GET /prices?sku=X&limit=N` — Same with custom page size (max 200)
- `POST /crawl/trigger` — Trigger new crawl run (async, returns run_id)
- `GET /crawl/status/{run_id}` — Check crawl run status

**Auth:**
- If `PULL_API_TOKEN` is set: Every request requires `Authorization: Bearer <token>`
- If unset: No auth required (development mode — warning logged at startup)

**Crawl Trigger Behavior:**
- Only one crawl runs at a time. Returns HTTP 409 if crawl already running.
- Crawl runs in background thread (asyncio.run() safe from thread context).
- Poll `/crawl/status/{run_id}` to check when crawl finishes.

### Core Modules

**Security (`security/`)**
- `env_loader.py` — Loads `.env` via `python-dotenv`, validates required keys at startup, returns frozen config object

**Crawling Engine (`scraper/`)**
- **spider.py** — Scrapy spider. Uses `scrapy-playwright` for JS pages, standard Scrapy for static HTML. Each request wrapped in try-except; exhausted retries yield `ErrorResult`.
- **parser.py** — Stateless price extractor. Tries CSS selector first, falls back to XPath. Strips currency symbols and thousands separators, converts to `float`. Returns `None` on parse failure.
- **retry.py** — Exponential backoff decorator (`base=1s`, `max=30s`, `max_retries=3`). Retries on: connection errors, HTTP 429/500/502/503, Playwright timeout. Does **not** retry on HTTP 404 or parse errors.
- **proxy.py** — Round-robin proxy rotation from `PROXY_LIST` env var (comma-separated). Marks dead proxies after N consecutive failures; falls back to direct connection if all proxies exhausted.
- **rate_limiter.py** — Per-domain token bucket with asyncio lock (thread-safe). Rate limit configured per-site in YAML/API.
- **scheduler.py** — Expands site configs × SKU lists into flat CrawlJob list.
- **api_server.py** — FastAPI server for Pull API. Spawns background threads for async crawls.

**Storage (`scraper/storage/`)**
- **database.py** — MySQL repository for crawl results and run metadata. Uses `mysql-connector-python` with connection pool.
- **models.py** — Data models for CrawlResult, CrawlRun, etc.

**Configuration (`scraper/config/`)**
- **settings.py** — Load YAML config (sites.yaml).
- **api_source.py** — Fetch and build jobs from CMS API.

---

## Environment Variables (`.env`)

### For CLI Mode (--source api)

| Variable | Description |
|----------|-------------|
| `CMS_API_URL` | CMS endpoint to fetch product list |
| `CMS_API_TOKEN` | Auth token for CMS API |

### For Server Mode (--serve)

| Variable | Default | Description |
|----------|---------|-------------|
| `PULL_API_HOST` | `0.0.0.0` | Host for API server to bind |
| `PULL_API_PORT` | `8080` | Port for API server |
| `PULL_API_TOKEN` | (none) | Bearer token for API auth. Leave empty for dev (no auth). |

### Database (Both Modes)

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `mysql://crawler:crawler@localhost:3306/price_crawler` | MySQL connection string |

### MySQL Docker Compose Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MYSQL_ROOT_PASSWORD` | `root_password` | MySQL root password |
| `MYSQL_DATABASE` | `price_crawler` | Database name to create |
| `MYSQL_USER` | `crawler` | MySQL user to create |
| `MYSQL_PASSWORD` | `crawler_password` | Password for MySQL user |

### Optional (Both Modes)

| Variable | Default | Description |
|----------|---------|-------------|
| `PROXY_LIST` | (none) | Comma-separated proxy URLs |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Key Constraints

- **Python 3.12** target
- **MySQL 8.0+** required
- **Chromium only** in Docker — no Firefox/WebKit (reduce image size)
- `parser.py` must remain stateless — no side effects, fully testable in isolation
- Retry logic must **not** retry on HTTP 404 or parse failures — these are valid `Error` records
- Pull API crawl trigger runs in background thread (asyncio.run() safe from thread context)
- Docker container runs as **non-root user**
- Integration tests use `testcontainers[mysql]` — Docker must be running to run `pytest -m integration`
- **AES encryption removed** (as of commit c13aba5) — no encryption layer in current version
