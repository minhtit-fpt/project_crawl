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

---

## Risks Tracked

- **HIGH:** Anti-bot detection (Playwright fingerprinting)
- **HIGH:** Scrapy + Playwright integration complexity
- **MEDIUM:** CSS/XPath selectors break on site redesign
- **MEDIUM:** Docker image size (~500MB with Playwright)
- **MEDIUM:** AES key management in production

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
1. Confirm plan → `git init` + tạo branch `phase/1-foundation`
2. Phase 1: requirements.txt, .env.example, env_loader.py, encryption.py
3. Phase 2: sites.yaml, settings.py, scheduler.py
4. Phase 3: rate_limiter.py, proxy.py, retry.py, spider.py
5. Phase 4: parser.py, output.py
6. Phase 5: main.py (integration)
7. Phase 6: Dockerfile, docker-compose.yml
8. Phase 7: Tests (80%+ coverage)

---

## Notes
- Project dir: `D:\Project\project_claw`
- All secrets must use `.env` (never hardcode)
- Output format: `Price | SKU | Source | Timestamp | Status`
