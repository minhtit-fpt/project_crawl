"""
Per-domain rate limiter.

Enforces a minimum delay between consecutive requests to the same domain.
Thread-safe via asyncio.Lock — one lock per domain, created lazily.

Usage:
    limiter = RateLimiter()
    await limiter.wait("example.com", delay_seconds=2.0)
    # ... fire request ...
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


class RateLimiter:
    """Asyncio-compatible per-domain rate limiter.

    Tracks the last request timestamp per domain and sleeps for the
    remaining delay before allowing the next request through.
    """

    def __init__(self) -> None:
        # domain -> last request monotonic timestamp
        self._last_request: dict[str, float] = defaultdict(float)
        # domain -> asyncio lock (ensures one waiter at a time per domain)
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def wait(self, url: str, delay_seconds: float) -> None:
        """Block until it is safe to send a request to the given URL's domain.

        Args:
            url:           Full URL — only the netloc (domain) portion is used.
            delay_seconds: Minimum seconds between requests to this domain.
        """
        domain = _extract_domain(url)

        async with self._locks[domain]:
            now = time.monotonic()
            elapsed = now - self._last_request[domain]
            remaining = delay_seconds - elapsed

            if remaining > 0:
                logger.debug(
                    "RateLimiter: sleeping %.2fs for domain '%s'", remaining, domain
                )
                await asyncio.sleep(remaining)

            self._last_request[domain] = time.monotonic()

    def reset(self, domain: str | None = None) -> None:
        """Reset rate limit state. Pass a domain to reset only that domain,
        or None to reset all.
        """
        if domain is None:
            self._last_request.clear()
        else:
            self._last_request.pop(domain, None)


def _extract_domain(url: str) -> str:
    """Return the netloc from a URL, falling back to the raw string on failure."""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url
