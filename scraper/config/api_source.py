"""
CMS API source — fetch crawl targets dynamically instead of reading sites.yaml.

The CMS API returns a paginated list of products. Each product has a SKU and a
list of exact URLs to scrape for pricing. This module:

  1. Fetches all pages from the API (handles pagination via totalPages).
  2. Validates the response shape — fails fast on bad data.
  3. Converts each (sku, url) pair into a CrawlJob with selectors=None so the
     spider uses auto-detect price extraction (see parser.extract_price_auto).

Expected API response shape:
  {
    "data": [
      {
        "title": "Product Name",
        "sku": "SKU001",
        "crawler": ["https://site-a.com/...", "https://site-b.com/..."]
      },
      ...
    ],
    "totals": 6,
    "totalPages": 1
  }

Auth: GET request with header  token: <CMS_API_TOKEN>
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from scraper.scheduler import CrawlJob

logger = logging.getLogger(__name__)

# Default rate limit applied to all API-sourced jobs (seconds between requests
# to the same domain). Conservative default — sites may tighten on first run.
DEFAULT_RATE_LIMIT_SECONDS: float = 2.0

# All API-sourced jobs use JS rendering by default (safer for unknown sites).
DEFAULT_REQUIRES_JS: bool = True

# Seconds before an API request times out.
API_REQUEST_TIMEOUT: int = 30


class ApiSourceError(Exception):
    """Raised when the CMS API is unreachable, returns an error, or has bad data."""


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ApiProduct:
    """One product entry as returned by the CMS API."""
    title: str
    sku: str
    crawler_urls: list[str]


# ── Public API ─────────────────────────────────────────────────────────────────

def fetch_products(api_url: str, api_token: str) -> list[ApiProduct]:
    """Fetch all products from the CMS API, following pagination.

    Args:
        api_url:   Full URL of the API endpoint.
        api_token: Auth token sent as the ``token`` request header.

    Returns:
        Flat list of ApiProduct across all pages.

    Raises:
        ApiSourceError: On connection failure, HTTP error, or invalid response.
    """
    headers = {"token": api_token}
    products: list[ApiProduct] = []
    page = 1

    while True:
        data = _fetch_page(api_url, headers, page)
        total_pages = _extract_total_pages(data)
        page_items = _extract_items(data, page)
        products.extend(page_items)

        logger.debug(
            "API page %d/%d fetched: %d products", page, total_pages, len(page_items)
        )

        if page >= total_pages:
            break
        page += 1

    logger.info("Fetched %d products from CMS API (%d pages)", len(products), page)
    return products


def build_jobs_from_api(products: list[ApiProduct]) -> list[CrawlJob]:
    """Convert API products into CrawlJob objects ready for the spider.

    Each (sku, crawler_url) pair becomes one CrawlJob with selectors=None
    so the spider falls back to auto-detect price extraction.

    Args:
        products: List of ApiProduct from fetch_products().

    Returns:
        List of CrawlJob (one per sku × crawler_url combination).
    """
    jobs: list[CrawlJob] = []

    for product in products:
        if not product.crawler_urls:
            logger.warning("SKU=%s has no crawler URLs — skipped", product.sku)
            continue

        for url in product.crawler_urls:
            domain = _domain_from_url(url)
            jobs.append(
                CrawlJob(
                    site_name=domain,
                    url=url,
                    sku=product.sku,
                    requires_js=DEFAULT_REQUIRES_JS,
                    rate_limit_seconds=DEFAULT_RATE_LIMIT_SECONDS,
                    selectors=None,  # auto-detect via extract_price_auto
                )
            )

    logger.info("Built %d crawl jobs from %d API products", len(jobs), len(products))
    return jobs


# ── Private helpers ────────────────────────────────────────────────────────────

def _fetch_page(api_url: str, headers: dict[str, str], page: int) -> dict:
    """Make a single GET request to the API and return the parsed JSON body."""
    params = {"page": page} if page > 1 else {}
    try:
        response = requests.get(
            api_url,
            headers=headers,
            params=params,
            timeout=API_REQUEST_TIMEOUT,
        )
    except requests.ConnectionError as exc:
        raise ApiSourceError(f"Cannot connect to CMS API at {api_url}: {exc}") from exc
    except requests.Timeout:
        raise ApiSourceError(
            f"CMS API request timed out after {API_REQUEST_TIMEOUT}s (url={api_url})"
        )
    except requests.RequestException as exc:
        raise ApiSourceError(f"CMS API request failed: {exc}") from exc

    if response.status_code in (401, 403):
        raise ApiSourceError(
            f"CMS API authentication failed (HTTP {response.status_code}). "
            "Check CMS_API_TOKEN in your .env file."
        )
    if response.status_code != 200:
        raise ApiSourceError(
            f"CMS API returned unexpected status {response.status_code} for {api_url}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise ApiSourceError(
            f"CMS API response is not valid JSON (url={api_url}): {exc}"
        ) from exc


def _extract_total_pages(data: dict) -> int:
    """Extract and validate totalPages from the API response."""
    if not isinstance(data, dict) or "data" not in data:
        raise ApiSourceError(
            "CMS API response missing required 'data' key. "
            f"Got keys: {list(data.keys()) if isinstance(data, dict) else type(data)}"
        )
    total_pages = data.get("totalPages", 1)
    if not isinstance(total_pages, int) or total_pages < 1:
        logger.warning("Unexpected totalPages=%r — defaulting to 1", total_pages)
        return 1
    return total_pages


def _extract_items(data: dict, page: int) -> list[ApiProduct]:
    """Parse the 'data' list from one API page into ApiProduct objects."""
    raw_items = data.get("data", [])
    if not isinstance(raw_items, list):
        raise ApiSourceError(
            f"CMS API 'data' field must be a list (page {page}), "
            f"got {type(raw_items).__name__}"
        )

    products: list[ApiProduct] = []
    for idx, item in enumerate(raw_items):
        if not isinstance(item, dict):
            logger.warning("Skipping non-dict item at data[%d] on page %d", idx, page)
            continue

        sku = item.get("sku")
        title = item.get("title", "")
        crawler_urls = item.get("crawler", [])

        if not sku or not isinstance(sku, str):
            logger.warning("Skipping item at data[%d] — missing or invalid 'sku'", idx)
            continue
        if not isinstance(crawler_urls, list):
            logger.warning("SKU=%s: 'crawler' is not a list — skipped", sku)
            continue

        # Filter out blank/non-string URLs silently
        valid_urls = [u for u in crawler_urls if isinstance(u, str) and u.strip()]
        products.append(ApiProduct(title=str(title), sku=sku.strip(), crawler_urls=valid_urls))

    return products


def _domain_from_url(url: str) -> str:
    """Extract the netloc (domain) from a URL for use as site_name."""
    try:
        return urlparse(url).netloc or url
    except Exception:
        return url
