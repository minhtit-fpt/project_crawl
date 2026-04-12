"""
Tests for scraper/spider.py — Scrapling-based price fetcher.

All external I/O (AsyncFetcher.get, DynamicFetcher.async_fetch) is mocked.
No live browser or network is required.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from scraper.proxy import ProxyManager
from scraper.rate_limiter import RateLimiter
from scraper.retry import RetryHandler
from scraper.scheduler import CrawlResult, ErrorResult
from scraper.spider import (
    CONCURRENCY_LIMIT,
    NON_RETRYABLE_STATUS,
    RETRYABLE_STATUS,
    _do_fetch,
    _fetch_job,
    _run_async,
    run_spider,
)
from scraper.config.settings import SelectorConfig


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def selectors():
    return SelectorConfig(price_css=".price", price_xpath="//span[@class='price']")


@pytest.fixture()
def proxy_manager():
    pm = MagicMock(spec=ProxyManager)
    pm.get_proxy.return_value = None
    return pm


@pytest.fixture()
def rate_limiter():
    rl = MagicMock(spec=RateLimiter)
    rl.wait = AsyncMock()
    return rl


@pytest.fixture()
def retry_handler():
    return RetryHandler(max_retries=0, base_delay=0.0)


@pytest.fixture()
def crawl_job(selectors):
    from scraper.scheduler import CrawlJob
    return CrawlJob(
        site_name="TestShop",
        url="https://example.com/SKU-001",
        sku="SKU-001",
        selectors=selectors,
        requires_js=False,
        rate_limit_seconds=0.0,
    )


@pytest.fixture()
def js_crawl_job(selectors):
    from scraper.scheduler import CrawlJob
    return CrawlJob(
        site_name="JSShop",
        url="https://jsshop.com/SKU-002",
        sku="SKU-002",
        selectors=selectors,
        requires_js=True,
        rate_limit_seconds=0.0,
    )


def _mock_page(status: int = 200, html: str = "<html><span class='price'>$9.99</span></html>"):
    """Create a mock Scrapling Response page."""
    page = MagicMock()
    page.status = status
    page.encoding = "utf-8"
    page.body = html.encode("utf-8")
    return page


# ── _do_fetch ─────────────────────────────────────────────────────────────────

class TestDoFetch:
    @pytest.mark.asyncio
    async def test_static_page_uses_async_fetcher(self, crawl_job):
        page = _mock_page()
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page) as mock_get:
            status, html = await _do_fetch(crawl_job, proxy=None)

        mock_get.assert_called_once()
        assert status == 200
        assert "price" in html

    @pytest.mark.asyncio
    async def test_js_page_uses_playwright_fetcher(self, js_crawl_job):
        page = _mock_page()
        with patch("scraper.spider.DynamicFetcher.async_fetch", new_callable=AsyncMock, return_value=page) as mock_fetch:
            status, html = await _do_fetch(js_crawl_job, proxy=None)

        mock_fetch.assert_called_once()
        assert status == 200

    @pytest.mark.asyncio
    async def test_proxy_forwarded_to_fetcher(self, crawl_job):
        page = _mock_page()
        proxy = "http://user:pass@proxy:8080"
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page) as mock_get:
            await _do_fetch(crawl_job, proxy=proxy)

        call_kwargs = mock_get.call_args.kwargs
        assert call_kwargs.get("proxy") == proxy

    @pytest.mark.asyncio
    async def test_none_encoding_falls_back_to_utf8(self, crawl_job):
        page = _mock_page()
        page.encoding = None
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            status, html = await _do_fetch(crawl_job, proxy=None)
        assert isinstance(html, str)

    @pytest.mark.asyncio
    async def test_network_idle_disabled_for_js_pages(self, js_crawl_job):
        """network_idle=False — heavy sites never reach idle; wait for load only."""
        page = _mock_page()
        with patch("scraper.spider.DynamicFetcher.async_fetch", new_callable=AsyncMock, return_value=page) as mock_fetch:
            await _do_fetch(js_crawl_job, proxy=None)

        call_kwargs = mock_fetch.call_args.kwargs
        assert call_kwargs.get("network_idle") is False
        assert call_kwargs.get("timeout") == 60_000
        assert call_kwargs.get("retries") == 1


# ── _fetch_job ────────────────────────────────────────────────────────────────

class TestFetchJob:
    @pytest.mark.asyncio
    async def test_successful_fetch_returns_crawl_result(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        page = _mock_page(html="<html><span class='price'>$19.99</span></html>")
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            with patch("scraper.spider.extract_price", return_value=19.99):
                result = await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        assert isinstance(result, CrawlResult)
        assert result.price == 19.99
        assert result.sku == "SKU-001"

    @pytest.mark.asyncio
    async def test_404_returns_error_result(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        page = _mock_page(status=404)
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            result = await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        assert isinstance(result, ErrorResult)
        assert "HTTP 404" in result.error

    @pytest.mark.asyncio
    async def test_410_returns_error_result(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        page = _mock_page(status=410)
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            result = await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        assert isinstance(result, ErrorResult)
        assert "HTTP 410" in result.error

    @pytest.mark.asyncio
    async def test_price_none_returns_error_result(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        page = _mock_page()
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            with patch("scraper.spider.extract_price", return_value=None):
                result = await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        assert isinstance(result, ErrorResult)
        assert "not found" in result.error.lower()

    @pytest.mark.asyncio
    async def test_parse_error_returns_error_result(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        page = _mock_page()
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            with patch("scraper.spider.extract_price", side_effect=ValueError("bad price")):
                result = await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        assert isinstance(result, ErrorResult)
        assert "ParseError" in result.error

    @pytest.mark.asyncio
    async def test_rate_limiter_called(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        page = _mock_page()
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            with patch("scraper.spider.extract_price", return_value=5.0):
                await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        rate_limiter.wait.assert_called_once_with(crawl_job.url, crawl_job.rate_limit_seconds)

    @pytest.mark.asyncio
    async def test_proxy_manager_get_proxy_called(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        page = _mock_page()
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            with patch("scraper.spider.extract_price", return_value=5.0):
                await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        proxy_manager.get_proxy.assert_called_once()

    @pytest.mark.asyncio
    async def test_network_error_marks_proxy_dead_and_returns_error(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        proxy_manager.get_proxy.return_value = "http://proxy:8080"
        with patch(
            "scraper.spider.AsyncFetcher.get",
            new_callable=AsyncMock,
            side_effect=ConnectionError("refused"),
        ):
            result = await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        proxy_manager.mark_dead.assert_called_once_with("http://proxy:8080")
        assert isinstance(result, ErrorResult)

    @pytest.mark.asyncio
    async def test_retryable_status_exhausts_retries_and_returns_error(
        self, crawl_job, proxy_manager, rate_limiter
    ):
        # Use RetryHandler with max_retries=1 to keep test fast
        retry_handler = RetryHandler(max_retries=1, base_delay=0.0)
        page = _mock_page(status=503)
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            result = await _fetch_job(crawl_job, proxy_manager, rate_limiter, retry_handler)

        assert isinstance(result, ErrorResult)
        assert "503" in result.error


# ── _run_async ────────────────────────────────────────────────────────────────

class TestRunAsync:
    @pytest.mark.asyncio
    async def test_callback_called_for_each_job(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        from scraper.scheduler import CrawlJob
        jobs = [
            CrawlJob("S1", "https://a.com/1", "A", crawl_job.selectors, False, 0.0),
            CrawlJob("S2", "https://b.com/2", "B", crawl_job.selectors, False, 0.0),
        ]
        results = []

        page = _mock_page()
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            with patch("scraper.spider.extract_price", return_value=1.0):
                await _run_async(jobs, results.append, proxy_manager, rate_limiter, retry_handler)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_empty_jobs_list(self, proxy_manager, rate_limiter, retry_handler):
        results = []
        await _run_async([], results.append, proxy_manager, rate_limiter, retry_handler)
        assert results == []

    @pytest.mark.asyncio
    async def test_one_failure_does_not_block_others(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        from scraper.scheduler import CrawlJob
        ok_job = CrawlJob("S1", "https://ok.com/1", "OK", crawl_job.selectors, False, 0.0)
        bad_job = CrawlJob("S2", "https://bad.com/2", "BAD", crawl_job.selectors, False, 0.0)

        ok_page = _mock_page()
        bad_page = _mock_page(status=404)

        async def fake_get(url, **kwargs):
            if "ok.com" in url:
                return ok_page
            return bad_page

        results = []
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, side_effect=fake_get):
            with patch("scraper.spider.extract_price", return_value=5.0):
                await _run_async(
                    [ok_job, bad_job], results.append, proxy_manager, rate_limiter, retry_handler
                )

        assert len(results) == 2
        statuses = {r.status for r in results}
        assert "OK" in statuses
        assert "Error" in statuses


# ── run_spider (public entry point) ──────────────────────────────────────────

class TestRunSpider:
    def test_blocking_call_collects_results(
        self, crawl_job, proxy_manager, rate_limiter, retry_handler
    ):
        results = []
        page = _mock_page()
        with patch("scraper.spider.AsyncFetcher.get", new_callable=AsyncMock, return_value=page):
            with patch("scraper.spider.extract_price", return_value=42.0):
                run_spider(
                    jobs=[crawl_job],
                    result_callback=results.append,
                    proxy_manager=proxy_manager,
                    rate_limiter=rate_limiter,
                    retry_handler=retry_handler,
                )

        assert len(results) == 1
        assert isinstance(results[0], CrawlResult)
        assert results[0].price == 42.0

    def test_empty_jobs_returns_no_results(
        self, proxy_manager, rate_limiter, retry_handler
    ):
        results = []
        run_spider(
            jobs=[],
            result_callback=results.append,
            proxy_manager=proxy_manager,
            rate_limiter=rate_limiter,
            retry_handler=retry_handler,
        )
        assert results == []


# ── Constants sanity check ────────────────────────────────────────────────────

class TestConstants:
    def test_non_retryable_includes_404_and_410(self):
        assert 404 in NON_RETRYABLE_STATUS
        assert 410 in NON_RETRYABLE_STATUS

    def test_retryable_includes_429_and_5xx(self):
        assert 429 in RETRYABLE_STATUS
        assert 500 in RETRYABLE_STATUS
        assert 503 in RETRYABLE_STATUS

    def test_concurrency_limit_positive(self):
        assert CONCURRENCY_LIMIT > 0
