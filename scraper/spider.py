"""
Scrapy spider with Playwright integration.

Receives CrawlJob objects from the Scheduler, fetches each page (using
Playwright for JS-heavy sites, plain Scrapy for static ones), delegates
price extraction to parser.py, and yields CrawlResult or ErrorResult.

Anti-detection measures applied:
  - Random user-agent rotation
  - Playwright stealth args (disable AutomationControlled flag)
  - Rate limiting via RateLimiter before each request
  - Proxy assignment via ProxyManager
  - Retry logic via RetryHandler for transient failures
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Generator

import scrapy
from scrapy import signals
from scrapy.http import Response

from scraper.config.settings import SelectorConfig
from scraper.proxy import ProxyManager
from scraper.rate_limiter import RateLimiter
from scraper.retry import NonRetryableError, RetryableHTTPError, RetryHandler
from scraper.scheduler import (
    CrawlJob,
    CrawlOutcome,
    CrawlResult,
    ErrorResult,
    make_crawl_result,
    make_error_result,
)

logger = logging.getLogger(__name__)

# HTTP status codes that are non-retryable (legitimate "not found")
NON_RETRYABLE_STATUS: frozenset[int] = frozenset({404, 410})

# Rotated user-agent pool
USER_AGENTS: list[str] = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]


class PriceSpider(scrapy.Spider):
    """Scrapy spider that crawls price data for a list of CrawlJobs.

    Instantiate with:
        spider = PriceSpider(
            jobs=[...],
            result_callback=scheduler.collect_result,
            proxy_manager=proxy_manager,
            rate_limiter=rate_limiter,
        )
    """

    name = "price_spider"

    # Disable Scrapy's built-in retry — we handle it in RetryHandler
    custom_settings: dict[str, Any] = {
        "RETRY_ENABLED": False,
    }

    def __init__(
        self,
        jobs: list[CrawlJob],
        result_callback: Callable[[CrawlOutcome], None],
        proxy_manager: ProxyManager,
        rate_limiter: RateLimiter,
        retry_handler: RetryHandler | None = None,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._jobs = jobs
        self._result_callback = result_callback
        self._proxy_manager = proxy_manager
        self._rate_limiter = rate_limiter
        self._retry_handler = retry_handler or RetryHandler()
        self._ua_index = 0

    # ── Scrapy lifecycle ───────────────────────────────────────────────────────

    def start_requests(self) -> Generator[scrapy.Request, None, None]:
        for job in self._jobs:
            yield self._build_request(job)

    def parse(self, response: Response, **kwargs: Any) -> Generator[CrawlOutcome, None, None]:
        job: CrawlJob = response.meta["job"]

        # Non-retryable HTTP status → ErrorResult immediately
        if response.status in NON_RETRYABLE_STATUS:
            outcome = make_error_result(
                sku=job.sku,
                source=job.site_name,
                error=f"HTTP {response.status}",
            )
            self._result_callback(outcome)
            yield outcome
            return

        # Retryable HTTP status → raise so Scrapy's errback handles it
        if response.status >= 400:
            raise RetryableHTTPError(response.status, response.url)

        # Extract price
        from scraper.parser import extract_price  # local import to avoid circular

        try:
            price = extract_price(response, job.selectors)
        except Exception as exc:
            outcome = make_error_result(
                sku=job.sku,
                source=job.site_name,
                error=f"ParseError: {exc}",
            )
            self._result_callback(outcome)
            yield outcome
            return

        if price is None:
            outcome = make_error_result(
                sku=job.sku,
                source=job.site_name,
                error="Price element not found",
            )
        else:
            outcome = make_crawl_result(
                sku=job.sku,
                price=price,
                source=job.site_name,
            )

        self._result_callback(outcome)
        yield outcome

    def errback(self, failure: Any) -> None:
        """Handle request-level failures (connection errors, timeouts, etc.)."""
        request = failure.request
        job: CrawlJob = request.meta["job"]
        proxy = request.meta.get("proxy")

        # Mark proxy as dead on network-level failures
        if proxy:
            self._proxy_manager.mark_dead(proxy)

        error_msg = str(failure.value)
        logger.warning(
            "Request failed for SKU=%s site=%s: %s",
            job.sku, job.site_name, error_msg,
        )

        outcome = make_error_result(
            sku=job.sku,
            source=job.site_name,
            error=error_msg,
        )
        self._result_callback(outcome)

    # ── Private helpers ────────────────────────────────────────────────────────

    def _build_request(self, job: CrawlJob) -> scrapy.Request:
        """Build a Scrapy Request for the given job, applying proxy and UA."""
        proxy = self._proxy_manager.get_proxy()
        user_agent = self._rotate_user_agent()

        meta: dict[str, Any] = {
            "job": job,
            "proxy": proxy,
        }

        if job.requires_js:
            meta["playwright"] = True
            meta["playwright_include_page"] = False
            meta["playwright_page_methods"] = []

        request = scrapy.Request(
            url=job.url,
            callback=self.parse,
            errback=self.errback,
            headers={"User-Agent": user_agent},
            meta=meta,
            dont_filter=True,
        )

        return request

    def _rotate_user_agent(self) -> str:
        ua = USER_AGENTS[self._ua_index % len(USER_AGENTS)]
        self._ua_index += 1
        return ua
