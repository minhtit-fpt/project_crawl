"""Tests for scraper/rate_limiter.py — per-domain RateLimiter."""

import asyncio
import time
import pytest

from scraper.rate_limiter import RateLimiter, _extract_domain


class TestExtractDomain:
    def test_standard_url(self):
        assert _extract_domain("https://example.com/path") == "example.com"

    def test_url_with_port(self):
        assert _extract_domain("http://example.com:8080/path") == "example.com:8080"

    def test_fallback_on_plain_string(self):
        result = _extract_domain("not-a-url")
        assert result == "not-a-url"


class TestRateLimiterWait:
    @pytest.mark.asyncio
    async def test_first_request_passes_immediately(self):
        limiter = RateLimiter()
        start = time.monotonic()
        await limiter.wait("https://example.com/page", delay_seconds=1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # no sleep on first request

    @pytest.mark.asyncio
    async def test_second_request_is_delayed(self):
        limiter = RateLimiter()
        await limiter.wait("https://example.com/page", delay_seconds=0.2)
        start = time.monotonic()
        await limiter.wait("https://example.com/other", delay_seconds=0.2)
        elapsed = time.monotonic() - start
        assert elapsed >= 0.15  # close to 0.2s delay

    @pytest.mark.asyncio
    async def test_different_domains_are_independent(self):
        limiter = RateLimiter()
        await limiter.wait("https://site-a.com/", delay_seconds=5.0)
        start = time.monotonic()
        # site-b has no history — should pass immediately
        await limiter.wait("https://site-b.com/", delay_seconds=5.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_zero_delay_never_sleeps(self):
        limiter = RateLimiter()
        await limiter.wait("https://example.com/", delay_seconds=0)
        start = time.monotonic()
        await limiter.wait("https://example.com/", delay_seconds=0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.05


class TestRateLimiterReset:
    @pytest.mark.asyncio
    async def test_reset_specific_domain_clears_history(self):
        limiter = RateLimiter()
        await limiter.wait("https://example.com/", delay_seconds=1.0)
        limiter.reset("example.com")
        # After reset, next request should pass immediately
        start = time.monotonic()
        await limiter.wait("https://example.com/", delay_seconds=1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_reset_all_clears_all_domains(self):
        limiter = RateLimiter()
        await limiter.wait("https://a.com/", delay_seconds=1.0)
        await limiter.wait("https://b.com/", delay_seconds=1.0)
        limiter.reset()
        start = time.monotonic()
        await limiter.wait("https://a.com/", delay_seconds=1.0)
        await limiter.wait("https://b.com/", delay_seconds=1.0)
        elapsed = time.monotonic() - start
        assert elapsed < 0.1
