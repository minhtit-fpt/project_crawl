"""
Price parser -- stateless price extraction from Scrapy responses.

Strategy:
  1. Try CSS selector (primary)
  2. Fall back to XPath selector if CSS yields nothing
  3. Strip HTML tags, detect number format, normalise to float

Supported formats:
  "$1,234.56"    -> 1234.56  (USD / en)
  "1.234,56 EUR" -> 1234.56  (EUR / de)
  "1.500.000d"   -> 1500000  (VND dot-thousands)
  "99.99"        -> 99.99
  "Price: 99"    -> 99.0

Returns None if no element found. Raises ValueError if element found but
text cannot be parsed (both become ErrorResult -- not retried).
"""
from __future__ import annotations

import re
import logging
from typing import Optional, Protocol

logger = logging.getLogger(__name__)

_NON_NUMERIC = re.compile(r"[^\d.,\-]")
_HTML_TAG = re.compile(r"<[^>]+>")


class ScrapyResponse(Protocol):
    def css(self, query: str): ...
    def xpath(self, query: str): ...


class SelectorConfig(Protocol):
    price_css: str
    price_xpath: str


def extract_price(response: ScrapyResponse, selectors: SelectorConfig) -> Optional[float]:
    """Extract and parse a price from a Scrapy response.

    Returns float or None (element not found). Raises ValueError on parse failure.
    """
    raw_text = _find_text(response, selectors)
    if raw_text is None:
        logger.debug(
            "No price element found with CSS=%r or XPath=%r",
            selectors.price_css, selectors.price_xpath,
        )
        return None
    return _parse_price_text(raw_text)


def _find_text(response: ScrapyResponse, selectors: SelectorConfig) -> Optional[str]:
    """Try CSS first, then XPath. Return stripped text or None."""
    try:
        text = response.css(selectors.price_css).get()
        if text and text.strip():
            return text.strip()
    except Exception as exc:
        logger.warning("CSS selector %r failed: %s", selectors.price_css, exc)

    try:
        text = response.xpath(selectors.price_xpath).get()
        if text and text.strip():
            return text.strip()
    except Exception as exc:
        logger.warning("XPath selector %r failed: %s", selectors.price_xpath, exc)

    return None


def _parse_price_text(raw: str) -> float:
    """Normalise a raw price string to float.

    Steps:
      1. Strip HTML tags
      2. Extract only digits, dots, commas, minus (preserve separators for detection)
      3. Detect number format and normalise separators
      4. Convert to float
    """
    # Step 1: strip HTML
    cleaned = _strip_html_tags(raw).strip()

    # Step 2: keep only numeric-relevant characters for separator detection
    #         but preserve original separators before removing currency symbols
    numeric_only = _NON_NUMERIC.sub("", cleaned).strip("-").strip()

    if not numeric_only:
        raise ValueError(f"No numeric content found in price text: {raw!r}")

    # Step 3: detect and normalise separators on the numeric-only string
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
        # else: standard decimal "1.56" -- leave as-is

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
