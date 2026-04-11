"""
Price parser -- stateless price extraction using Scrapling's adaptive parser.

Strategy:
  1. Build a Scrapling Adaptor from the raw HTML (auto_match=True enables
     adaptive fingerprinting — element is auto-relocated if site redesigns)
  2. Try CSS selector (primary) with auto_save to persist element fingerprint
  3. Fall back to XPath selector if CSS yields nothing
  4. Strip currency symbols / thousands separators, normalise to float

Adaptive healing: on first run Scrapling records where the price element lives
(tag, surrounding text, parent structure). If the site later moves the price to
a new CSS class, Scrapling's similarity matching finds it again without any
manual selector update.

Supported price formats:
  "$1,234.56"    -> 1234.56  (USD / en)
  "1.234,56 EUR" -> 1234.56  (EUR / de)
  "1.500.000đ"   -> 1500000  (VND dot-thousands)
  "99.99"        -> 99.99
  "Price: 99"    -> 99.0

Returns None if no element found. Raises ValueError if element found but
text cannot be parsed (both become ErrorResult — not retried).
"""
from __future__ import annotations

import re
import logging
from typing import Optional, Protocol

from scrapling.parser import Adaptor

logger = logging.getLogger(__name__)

_NON_NUMERIC = re.compile(r"[^\d.,\-]")
_HTML_TAG = re.compile(r"<[^>]+>")


class SelectorConfig(Protocol):
    price_css: str
    price_xpath: str


def extract_price(html: str, url: str, selectors: SelectorConfig) -> Optional[float]:
    """Extract and parse a price from a raw HTML page.

    Args:
        html:      Raw HTML string of the fetched page.
        url:       Page URL — used by Scrapling to namespace adaptive fingerprints
                   so each site's element memory is kept separate.
        selectors: CSS and XPath selector config from sites.yaml.

    Returns float or None (element not found). Raises ValueError on parse failure.
    """
    raw_text = _find_text(html, url, selectors)
    if raw_text is None:
        logger.debug(
            "No price element found with CSS=%r or XPath=%r",
            selectors.price_css, selectors.price_xpath,
        )
        return None
    return _parse_price_text(raw_text)


def _find_text(html: str, url: str, selectors: SelectorConfig) -> Optional[str]:
    """Use Scrapling Adaptor to find price text with adaptive CSS→XPath fallback.

    auto_match=True  — enables adaptive mode (fingerprint-based relocating).
    auto_save=True   — persists element fingerprint on every successful match,
                       so future runs can heal from selector breakage.
    """
    try:
        page = Adaptor(html, url=url, auto_match=True)
    except Exception as exc:
        logger.warning("Failed to build Scrapling Adaptor for %s: %s", url, exc)
        return None

    # ── CSS selector (primary) ─────────────────────────────────────────────────
    try:
        els = page.css(selectors.price_css, auto_save=True)
        if els:
            # get_all_text(separator='') joins text of nested child nodes too,
            # e.g. <span><b>$</b>99.99</span>  →  "$99.99"
            text = els[0].get_all_text(separator="")
            if text and text.strip():
                return text.strip()
    except Exception as exc:
        logger.warning("CSS selector %r failed: %s", selectors.price_css, exc)

    # ── XPath selector (fallback) ──────────────────────────────────────────────
    try:
        els = page.xpath(selectors.price_xpath, auto_save=True)
        if els:
            text = els[0].get_all_text(separator="")
            if text and text.strip():
                return text.strip()
    except Exception as exc:
        logger.warning("XPath selector %r failed: %s", selectors.price_xpath, exc)

    return None


def _parse_price_text(raw: str) -> float:
    """Normalise a raw price string to float.

    Steps:
      1. Strip HTML tags (defensive — text from Scrapling is already clean,
         but _parse_price_text is also called directly in tests)
      2. Extract only digits, dots, commas, minus
      3. Detect number format and normalise separators
      4. Convert to float
    """
    # Step 1: strip HTML
    cleaned = _strip_html_tags(raw).strip()

    # Step 2: keep only numeric-relevant characters
    numeric_only = _NON_NUMERIC.sub("", cleaned).strip("-").strip()

    if not numeric_only:
        raise ValueError(f"No numeric content found in price text: {raw!r}")

    # Step 3: detect and normalise separators
    has_comma = "," in numeric_only
    has_dot = "." in numeric_only

    if has_comma and has_dot:
        # Whichever separator comes last is the decimal separator
        if numeric_only.rfind(",") > numeric_only.rfind("."):
            # European: "1.234,56"
            numeric_only = numeric_only.replace(".", "").replace(",", ".")
        else:
            # US: "1,234.56"
            numeric_only = numeric_only.replace(",", "")
    elif has_comma and not has_dot:
        parts = numeric_only.split(",")
        if len(parts) == 2 and len(parts[1]) == 3:
            # Thousands: "1,234"
            numeric_only = numeric_only.replace(",", "")
        else:
            # Decimal: "1,56"
            numeric_only = numeric_only.replace(",", ".")
    elif has_dot and not has_comma:
        parts = numeric_only.split(".")
        # VND multi-dot or single dot-thousands: all parts after first are 3 digits
        if len(parts) >= 2 and all(len(p) == 3 for p in parts[1:]):
            numeric_only = numeric_only.replace(".", "")
        # else: standard decimal "1.56" — leave as-is

    # Step 4: final strip and convert
    numeric_only = numeric_only.strip("-").strip()
    if not numeric_only:
        raise ValueError(f"No numeric content found in price text: {raw!r}")

    try:
        return float(numeric_only)
    except ValueError:
        raise ValueError(
            f"Cannot convert price text to float: {raw!r} -> cleaned={numeric_only!r}"
        )


def _strip_html_tags(text: str) -> str:
    """Remove HTML tags: '<span>$99</span>' -> '$99'."""
    return _HTML_TAG.sub("", text)
