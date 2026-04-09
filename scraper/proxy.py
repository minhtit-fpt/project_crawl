"""
Proxy rotation manager.

Loads a list of proxy URLs from the AppConfig (originally from PROXY_LIST env
var), rotates them round-robin, and tracks dead proxies. If all proxies are
dead, falls back to a direct (no-proxy) connection with a warning.

Usage:
    manager = ProxyManager(config.proxy_list)
    proxy = manager.get_proxy()          # e.g. "http://user:pass@host:8080"
    manager.mark_dead(proxy)             # after consecutive failures
    manager.mark_alive(proxy)            # after a success
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Optional

logger = logging.getLogger(__name__)

# Number of consecutive failures before a proxy is marked dead
DEAD_THRESHOLD = 3

# Number of successful requests before a dead proxy is re-admitted
RECOVERY_THRESHOLD = 1


class ProxyManager:
    """Round-robin proxy rotation with dead-proxy tracking.

    Thread-safe via a reentrant lock so it can be called from
    multiple Scrapy downloader threads simultaneously.
    """

    def __init__(self, proxy_list: list[str]) -> None:
        self._proxies: list[str] = list(proxy_list)
        self._lock = threading.RLock()
        self._index: int = 0
        # proxy -> consecutive failure count
        self._failures: dict[str, int] = defaultdict(int)
        # proxies currently considered dead
        self._dead: set[str] = set()

        if not self._proxies:
            logger.warning(
                "ProxyManager: no proxies configured — all requests will use "
                "a direct connection. Set PROXY_LIST in .env to enable rotation."
            )

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_proxy(self) -> Optional[str]:
        """Return the next available proxy URL, or None for a direct connection.

        Skips dead proxies. If every proxy is dead, logs a warning and returns
        None (direct connection).
        """
        with self._lock:
            if not self._proxies:
                return None

            live = [p for p in self._proxies if p not in self._dead]

            if not live:
                logger.warning(
                    "ProxyManager: all %d proxies are dead — falling back to "
                    "direct connection",
                    len(self._proxies),
                )
                return None

            proxy = live[self._index % len(live)]
            self._index = (self._index + 1) % len(live)
            return proxy

    def mark_dead(self, proxy: str) -> None:
        """Record a failure for a proxy. Marks it dead after DEAD_THRESHOLD failures."""
        with self._lock:
            self._failures[proxy] += 1
            if self._failures[proxy] >= DEAD_THRESHOLD and proxy not in self._dead:
                self._dead.add(proxy)
                logger.warning(
                    "ProxyManager: proxy marked dead after %d failures: %s",
                    self._failures[proxy],
                    _mask_proxy(proxy),
                )

    def mark_alive(self, proxy: str) -> None:
        """Record a success for a proxy. Re-admits it if it was dead."""
        with self._lock:
            self._failures[proxy] = 0
            if proxy in self._dead:
                self._dead.discard(proxy)
                logger.info(
                    "ProxyManager: proxy recovered and re-admitted: %s",
                    _mask_proxy(proxy),
                )

    @property
    def live_count(self) -> int:
        with self._lock:
            return len(self._proxies) - len(self._dead)

    @property
    def dead_count(self) -> int:
        with self._lock:
            return len(self._dead)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _mask_proxy(proxy: str) -> str:
    """Replace password in proxy URL with *** for safe logging."""
    try:
        from urllib.parse import urlparse, urlunparse
        parsed = urlparse(proxy)
        if parsed.password:
            masked = parsed._replace(
                netloc=f"{parsed.username}:***@{parsed.hostname}:{parsed.port}"
            )
            return urlunparse(masked)
    except Exception:
        pass
    return proxy
