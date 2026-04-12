"""
Retry handler with exponential backoff.

Wraps an async callable and retries it on transient failures.
Non-retryable conditions (HTTP 404, parse errors) pass through immediately
and become ErrorResult records — they are NOT retried.

Retryable conditions:
  - ConnectionError, TimeoutError, OSError
  - playwright._impl._errors.TimeoutError (JS page navigation timeout)
  - HTTP status codes: 429, 500, 502, 503, 504

Non-retryable (raise immediately → ErrorResult):
  - HTTP 404 (page genuinely not found)
  - ValueError from parser (price element present but unparseable)

Usage:
    handler = RetryHandler(max_retries=3, base_delay=1.0, max_delay=30.0)

    result = await handler.run(my_async_func, arg1, arg2)
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Callable, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# HTTP status codes that warrant a retry
RETRYABLE_STATUS_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})

# playwright.TimeoutError does NOT inherit from Python's builtin TimeoutError,
# so it must be added explicitly. Guard with try/except in case playwright is
# not installed (e.g. in test environments without browser deps).
try:
    from playwright._impl._errors import TimeoutError as _PlaywrightTimeoutError
except ImportError:
    _PlaywrightTimeoutError = None  # type: ignore[assignment]

# Exception types that always trigger a retry
RETRYABLE_EXCEPTIONS: tuple[type[Exception], ...] = tuple(
    exc for exc in (
        ConnectionError,
        TimeoutError,        # Python builtin
        OSError,
        _PlaywrightTimeoutError,  # Playwright JS timeout (different hierarchy)
    )
    if exc is not None
)


class RetryableHTTPError(Exception):
    """Raised by the spider when it receives a retryable HTTP status code."""

    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(f"HTTP {status_code} for {url}")


class NonRetryableError(Exception):
    """Raised when the error is definitive and should not be retried (e.g. 404)."""


class RetryHandler:
    """Executes an async callable with exponential backoff retries.

    Args:
        max_retries:  Maximum number of retry attempts (not counting the first try).
        base_delay:   Initial sleep duration in seconds before the first retry.
        max_delay:    Upper cap on sleep duration.
        jitter:       If True, adds ±20% random jitter to each delay.
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        jitter: bool = True,
    ) -> None:
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.jitter = jitter

    async def run(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Call func(*args, **kwargs) and retry on transient failures.

        Args:
            func:   Async callable to execute.
            *args:  Positional arguments forwarded to func.
            **kwargs: Keyword arguments forwarded to func.

        Returns:
            The return value of func on success.

        Raises:
            NonRetryableError: Immediately on non-retryable failures.
            Exception:         The last exception after all retries are exhausted.
        """
        last_exc: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)

            except NonRetryableError:
                raise  # pass through immediately — no retry

            except RETRYABLE_EXCEPTIONS as exc:
                last_exc = exc
                logger.warning(
                    "Attempt %d/%d failed (%s: %s)",
                    attempt + 1, self.max_retries + 1,
                    type(exc).__name__, exc,
                )

            except RetryableHTTPError as exc:
                last_exc = exc
                logger.warning(
                    "Attempt %d/%d — retryable HTTP %d for %s",
                    attempt + 1, self.max_retries + 1,
                    exc.status_code, exc.url,
                )

            except Exception as exc:
                # Unknown exception — treat as non-retryable to avoid infinite loops
                logger.error(
                    "Attempt %d/%d — unexpected error (%s: %s), not retrying",
                    attempt + 1, self.max_retries + 1,
                    type(exc).__name__, exc,
                )
                raise

            if attempt < self.max_retries:
                delay = self._compute_delay(attempt)
                logger.debug("Retrying in %.2fs (attempt %d)", delay, attempt + 2)
                await asyncio.sleep(delay)

        assert last_exc is not None
        logger.error(
            "All %d attempts exhausted. Last error: %s",
            self.max_retries + 1, last_exc,
        )
        raise last_exc

    # ── Private ────────────────────────────────────────────────────────────────

    def _compute_delay(self, attempt: int) -> float:
        """Exponential backoff: base * 2^attempt, capped at max_delay, ± jitter."""
        delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        if self.jitter:
            delay *= random.uniform(0.8, 1.2)
        return delay
