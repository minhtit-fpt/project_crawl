"""Tests for scraper/retry.py — RetryHandler with exponential backoff."""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch

from scraper.retry import (
    RetryHandler,
    NonRetryableError,
    RetryableHTTPError,
    RETRYABLE_STATUS_CODES,
)


class TestRetryHandlerInit:
    def test_default_values(self):
        h = RetryHandler()
        assert h.max_retries == 3
        assert h.base_delay == 1.0
        assert h.max_delay == 30.0
        assert h.jitter is True

    def test_custom_values(self):
        h = RetryHandler(max_retries=5, base_delay=2.0, max_delay=60.0, jitter=False)
        assert h.max_retries == 5
        assert h.base_delay == 2.0

    def test_negative_max_retries_raises(self):
        with pytest.raises(ValueError):
            RetryHandler(max_retries=-1)


class TestRetryHandlerSuccess:
    @pytest.mark.asyncio
    async def test_returns_value_on_first_try(self):
        handler = RetryHandler(max_retries=3)
        async def func():
            return 42
        result = await handler.run(func)
        assert result == 42

    @pytest.mark.asyncio
    async def test_passes_args_and_kwargs(self):
        handler = RetryHandler()
        async def func(a, b, *, c=0):
            return a + b + c
        result = await handler.run(func, 1, 2, c=3)
        assert result == 6


class TestRetryHandlerRetries:
    @pytest.mark.asyncio
    async def test_retries_on_connection_error(self):
        handler = RetryHandler(max_retries=2, base_delay=0, jitter=False)
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("refused")
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.run(func)

        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_timeout_error(self):
        handler = RetryHandler(max_retries=1, base_delay=0, jitter=False)
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError("timed out")
            return "done"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.run(func)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_retries_on_retryable_http_error(self):
        handler = RetryHandler(max_retries=2, base_delay=0, jitter=False)
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RetryableHTTPError(503, "https://example.com")
            return "ok"

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await handler.run(func)
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries_exhausted(self):
        handler = RetryHandler(max_retries=2, base_delay=0, jitter=False)

        async def func():
            raise ConnectionError("always fails")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError):
                await handler.run(func)

    @pytest.mark.asyncio
    async def test_call_count_equals_max_retries_plus_one(self):
        handler = RetryHandler(max_retries=3, base_delay=0, jitter=False)
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("fail")

        with patch("asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError):
                await handler.run(func)

        assert call_count == 4  # 1 initial + 3 retries


class TestNonRetryableError:
    @pytest.mark.asyncio
    async def test_non_retryable_error_not_retried(self):
        handler = RetryHandler(max_retries=3, base_delay=0, jitter=False)
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            raise NonRetryableError("HTTP 404")

        with pytest.raises(NonRetryableError):
            await handler.run(func)

        assert call_count == 1  # called exactly once, no retries

    @pytest.mark.asyncio
    async def test_unknown_exception_not_retried(self):
        handler = RetryHandler(max_retries=3, base_delay=0, jitter=False)
        call_count = 0

        async def func():
            nonlocal call_count
            call_count += 1
            raise RuntimeError("unexpected")

        with pytest.raises(RuntimeError):
            await handler.run(func)

        assert call_count == 1


class TestBackoffDelay:
    def test_compute_delay_increases_with_attempts(self):
        handler = RetryHandler(base_delay=1.0, max_delay=60.0, jitter=False)
        d0 = handler._compute_delay(0)
        d1 = handler._compute_delay(1)
        d2 = handler._compute_delay(2)
        assert d0 < d1 < d2

    def test_compute_delay_capped_at_max(self):
        handler = RetryHandler(base_delay=1.0, max_delay=5.0, jitter=False)
        assert handler._compute_delay(10) == 5.0

    def test_jitter_produces_variation(self):
        handler = RetryHandler(base_delay=1.0, max_delay=60.0, jitter=True)
        delays = {handler._compute_delay(1) for _ in range(20)}
        assert len(delays) > 1  # jitter means not all delays are identical


class TestRetryableHTTPError:
    def test_status_code_stored(self):
        err = RetryableHTTPError(503, "https://example.com")
        assert err.status_code == 503
        assert "503" in str(err)

    def test_retryable_status_codes_set(self):
        assert 429 in RETRYABLE_STATUS_CODES
        assert 500 in RETRYABLE_STATUS_CODES
        assert 502 in RETRYABLE_STATUS_CODES
        assert 503 in RETRYABLE_STATUS_CODES
        assert 504 in RETRYABLE_STATUS_CODES
        assert 404 not in RETRYABLE_STATUS_CODES
