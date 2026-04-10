"""
SKU Resolver — resolves short product codes to full product URLs.

When a site uses sku_mode="search", the resolver fetches the site's
search page and extracts the first product URL from the HTML results.

Strategy:
  - sku_mode="direct" (default): substitute {sku} into base_url immediately.
  - sku_mode="search": GET search_url → parse result_link_css → return first href.

This module is stateless: no caching, no side effects beyond the HTTP request.
Uses only requests + parsel, both already pulled in by Scrapy.
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from parsel import Selector

from scraper.config.settings import SiteConfig

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8",
}
_TIMEOUT_SECONDS = 10


def resolve_sku_to_url(sku: str, site: SiteConfig) -> Optional[str]:
    """Resolve a short SKU to the full product URL for the given site.

    Args:
        sku:  The product code entered by the user (e.g. "NIS-C09R2T28").
        site: The site configuration containing sku_mode and search config.

    Returns:
        Absolute product URL string, or None if resolution failed.
    """
    if site.sku_mode == "direct":
        return site.base_url.replace("{sku}", sku)

    if site.sku_mode == "search":
        return _resolve_via_search(sku, site)

    logger.error("Unknown sku_mode=%r for site %s — treating as direct", site.sku_mode, site.name)
    return site.base_url.replace("{sku}", sku)


# ── Private ────────────────────────────────────────────────────────────────────

def _resolve_via_search(sku: str, site: SiteConfig) -> Optional[str]:
    """Fetch the search page and extract the first product link."""
    if not site.search_url or not site.search_selectors:
        logger.error(
            "Site %s: sku_mode='search' but search_url or search_selectors is missing",
            site.name,
        )
        return None

    search_url = site.search_url.replace("{sku}", sku)
    logger.info("Resolving SKU=%s on %s via: %s", sku, site.name, search_url)

    try:
        resp = requests.get(search_url, headers=_HEADERS, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("Search request failed for SKU=%s site=%s: %s", sku, site.name, exc)
        return None

    sel = Selector(text=resp.text)
    css = site.search_selectors.result_link_css + "::attr(href)"
    hrefs = sel.css(css).getall()

    if not hrefs:
        logger.warning(
            "No results found for SKU=%s on %s (selector=%r) — "
            "dumping search HTML to debug_search_%s.html",
            sku, site.name, site.search_selectors.result_link_css, sku,
        )
        _dump_debug_html(sku, resp.text)
        return None

    resolved = _make_absolute(hrefs[0].strip(), site.base_url)
    logger.info("Resolved SKU=%s → %s", sku, resolved)
    return resolved


def _make_absolute(href: str, base_url: str) -> str:
    """Convert a relative href to an absolute URL using the site's base."""
    if href.startswith("http"):
        return href
    parsed = urlparse(base_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    return urljoin(origin, href)


def _dump_debug_html(sku: str, html: str) -> None:
    """Write HTML to a debug file for selector inspection."""
    path = f"debug_search_{sku}.html"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Debug HTML saved to %s", path)
    except OSError as exc:
        logger.debug("Could not write debug file: %s", exc)
