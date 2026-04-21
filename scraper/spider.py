"""
Scrapling-based price fetcher.

Replaces Scrapy + scrapy-playwright with Scrapling's native async fetchers.
Benefits:
  - No Scrapy event-loop / Twisted reactor complexity
  - Full asyncio — no "Event loop closed" noise at shutdown
  - Mockable for unit tests without a live browser process

Strategy per job:
  - requires_js=True  → DynamicFetcher.async_fetch() (Chromium, wait for load)
  - requires_js=False → AsyncFetcher.get()            (httpx, stealth headers)

Concurrency is bounded by CONCURRENCY_LIMIT to avoid overwhelming targets.
Rate limiting, proxy rotation, and retry logic are reused from existing modules.

JS fetch tuning:
  - network_idle=False: don't wait for full network idle (heavy sites never idle)
  - timeout=60000: 60s budget — generous for slow e-commerce sites
  - retries=1: minimum allowed; RetryHandler owns outer retry logic
  - disable_resources omitted: some sites use resource loading as anti-bot signal
"""

from __future__ import annotations

import asyncio
import logging
from typing import Callable

from scrapling.fetchers import AsyncFetcher, DynamicFetcher

from scraper.parser import extract_price, extract_price_auto
from scraper.proxy import ProxyManager
from scraper.rate_limiter import RateLimiter
from scraper.retry import NonRetryableError, RetryableHTTPError, RetryHandler
from scraper.scheduler import (
    CrawlJob,
    CrawlOutcome,
    make_crawl_result,
    make_error_result,
)

logger = logging.getLogger(__name__)

# HTTP statuses that are definitive failures — no retry
NON_RETRYABLE_STATUS: frozenset[int] = frozenset({404, 410})

# HTTP statuses that warrant a retry with backoff
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# Max simultaneous in-flight requests across all domains.
# Keep low for JS pages — multiple Chromium tabs to the same domain
# raises anti-bot flags and exhausts memory faster.
CONCURRENCY_LIMIT: int = 3


# ── Internal fetch helpers ─────────────────────────────────────────────────────

async def _do_fetch(job: CrawlJob, proxy: str | None) -> tuple[int, str]:
    """Perform a single HTTP fetch and return (status_code, html_string).

    Uses PlayWrightFetcher for JS-required pages, AsyncFetcher otherwise.
    Both accept proxy as a plain URL string (or None).
    """
    if job.requires_js:
        page = await DynamicFetcher.async_fetch(
            job.url,
            proxy=proxy,
            network_idle=False,   # don't wait for idle — heavy sites never idle
            timeout=15_000,       # 15s per page
            retries=1,            # Minimum allowed; RetryHandler manages outer retries
            headless=True,
            # NOTE: disable_resources intentionally omitted — some sites (e.g.
            # dienmayxanh.com) use resource loading as an anti-bot signal and
            # will block headless browsers that drop fonts/images/stylesheets.
        )
    else:
        page = await AsyncFetcher.get(
            job.url,
            proxy=proxy,
            stealthy_headers=True,
        )

    html = page.body.decode(page.encoding or "utf-8", errors="replace")
    return page.status, html


async def _fetch_job(
    job: CrawlJob,
    proxy_manager: ProxyManager,
    rate_limiter: RateLimiter,
    retry_handler: RetryHandler,
) -> CrawlOutcome:
    """Fetch, parse, and return a CrawlOutcome for one job.

    Applies rate limiting before the first attempt, then delegates retries
    to RetryHandler. Proxy is selected once and reused across retry attempts.
    """
    await rate_limiter.wait(job.url, job.rate_limit_seconds)
    proxy = proxy_manager.get_proxy()

    async def _attempt() -> CrawlOutcome:
        status, html = await _do_fetch(job, proxy)

        if status in NON_RETRYABLE_STATUS:
            raise NonRetryableError(f"HTTP {status}")

        if status in RETRYABLE_STATUS:
            raise RetryableHTTPError(status, job.url)

        try:
            if job.selectors is not None:
                price = extract_price(html, job.url, job.selectors)
            else:
                price = extract_price_auto(html, job.url)
        except ValueError as exc:
            raise NonRetryableError(f"ParseError: {exc}") from exc

        if price is None:
            raise NonRetryableError("Price element not found")

        return make_crawl_result(sku=job.sku, price=price, source=job.site_name)

    try:
        return await retry_handler.run(_attempt)
    except NonRetryableError as exc:
        return make_error_result(sku=job.sku, source=job.site_name, error=str(exc))
    except Exception as exc:
        # Network-level or unknown failure — mark proxy dead if one was used
        if proxy:
            proxy_manager.mark_dead(proxy)
        logger.warning(
            "Job failed SKU=%s site=%s: %s", job.sku, job.site_name, exc
        )
        return make_error_result(sku=job.sku, source=job.site_name, error=str(exc))


# ── Async runner ───────────────────────────────────────────────────────────────

async def _run_async(
    jobs: list[CrawlJob],
    result_callback: Callable[[CrawlOutcome], None],
    proxy_manager: ProxyManager,
    rate_limiter: RateLimiter,
    retry_handler: RetryHandler,
) -> None:
    """Run all jobs concurrently (bounded by CONCURRENCY_LIMIT)."""
    semaphore = asyncio.Semaphore(CONCURRENCY_LIMIT)

    async def _bounded(job: CrawlJob) -> None:
        async with semaphore:
            outcome = await _fetch_job(job, proxy_manager, rate_limiter, retry_handler)
            result_callback(outcome)

    await asyncio.gather(*[_bounded(job) for job in jobs])


# ── Public entry point ─────────────────────────────────────────────────────────

def run_spider(
    jobs: list[CrawlJob],
    result_callback: Callable[[CrawlOutcome], None],
    proxy_manager: ProxyManager,
    rate_limiter: RateLimiter,
    retry_handler: RetryHandler,
) -> None:
    """Blocking entry point — fetches all jobs and calls result_callback for each.

    Designed to be called from a synchronous context (main.py).
    Internally runs an asyncio event loop via asyncio.run().
    """
    asyncio.run(
        _run_async(jobs, result_callback, proxy_manager, rate_limiter, retry_handler)
    )
