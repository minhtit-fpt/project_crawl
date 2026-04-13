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

import json
import re
import logging
from typing import Optional, Protocol

from scrapling.parser import Adaptor

logger = logging.getLogger(__name__)

_NON_NUMERIC = re.compile(r"[^\d.,\-]")
_HTML_TAG = re.compile(r"<[^>]+>")
_JSON_LD_TAG = re.compile(r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE)

# Common Vietnamese e-commerce price selectors tried in order.
# Ordered from most-specific (structured data attributes) to most-generic.
# IMPORTANT: more-specific selectors must come BEFORE generic ones to avoid
# capturing mixed text (e.g. WooCommerce .price contains both old and new price).
_AUTO_CSS_SELECTORS = (
    "[itemprop='price']",
    "[itemprop='offers'] [itemprop='price']",
    ".box-price__selling",              # dienmayxanh.com, mediamart.vn
    "[class*='price__main__1']",        # dienmayan.vn (BEM pattern)
    ".price ins span",                  # WooCommerce sale price (dienmaytamanh.vn)
    ".price ins",                       # WooCommerce sale price container
    ".item-price",                      # muahangtaikho.vn
    ".product-price",
    ".price-current",
    ".sale-price",
    ".special-price .price",
    ".special-price",
    ".current-price",
    ".box-price",
    "span.price",
    "p.price",
    ".price",
)


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


def extract_price_auto(html: str, url: str) -> Optional[float]:
    """Auto-detect and extract a price from an unknown e-commerce page.

    Used when no selector config is available (e.g. jobs sourced from CMS API).

    Strategy (in order of reliability):
      1. JSON-LD structured data (schema.org Product / Offer)
      2. Schema.org microdata attribute (itemprop="price")
      3. Common Vietnamese e-commerce CSS patterns
      4. Returns None if all strategies fail — caller converts to ErrorResult

    Does NOT raise ValueError — any parse error returns None so callers
    can treat it as a soft failure without crashing the crawl.
    """
    # ── 1. JSON-LD ─────────────────────────────────────────────────────────────
    price = _extract_from_json_ld(html)
    if price is not None:
        logger.debug("auto-detect JSON-LD price=%s url=%s", price, url)
        return price

    # ── 2 & 3. DOM-based (microdata attr + CSS heuristics) ────────────────────
    try:
        page = Adaptor(html, url=url, auto_match=True)
    except Exception as exc:
        logger.warning("auto-detect: Adaptor failed for %s: %s", url, exc)
        return None

    for selector in _AUTO_CSS_SELECTORS:
        try:
            els = page.css(selector, auto_save=True)
            if not els:
                continue
            text = els[0].get_all_text(separator="").strip()
            if not text:
                continue
            try:
                parsed = _parse_price_text(text)
                logger.debug("auto-detect CSS %r price=%s url=%s", selector, parsed, url)
                return parsed
            except ValueError:
                continue
        except Exception as exc:
            logger.debug("auto-detect CSS %r error: %s", selector, exc)

    logger.debug("auto-detect: no price found for %s", url)
    return None


def _extract_from_json_ld(html: str) -> Optional[float]:
    """Extract price from JSON-LD <script> blocks (schema.org Product/Offer).

    Handles: direct price key, offers.price, and priceSpecification arrays.
    For priceSpecification, prefers entries without a priceType (i.e. the
    actual selling price) over ListPrice entries.
    Returns None on any error or if no price field is found.
    """
    for match in _JSON_LD_TAG.finditer(html):
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue

        # Normalise: wrap single object into list for uniform processing
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                continue

            # schema.org Product with direct "price" key
            price_raw = item.get("price")
            if price_raw is not None:
                try:
                    return _parse_price_text(str(price_raw))
                except ValueError:
                    pass

            # schema.org Product → offers → price
            offers = item.get("offers")
            if isinstance(offers, dict):
                offers = [offers]
            if isinstance(offers, list):
                for offer in offers:
                    if not isinstance(offer, dict):
                        continue
                    price_raw = offer.get("price")
                    if price_raw is not None:
                        try:
                            return _parse_price_text(str(price_raw))
                        except ValueError:
                            pass

            # schema.org priceSpecification (used by dieuhoa.vip, dienmaytamanh.vn)
            price = _extract_from_price_specification(item.get("priceSpecification"))
            if price is not None:
                return price

    return None


_LIST_PRICE_TYPE = "https://schema.org/ListPrice"


def _extract_from_price_specification(spec: object) -> Optional[float]:
    """Extract the selling price from a priceSpecification value.

    Prefers entries without priceType (actual sale price) over ListPrice.
    Falls back to the first parseable entry if all have a priceType.
    """
    if isinstance(spec, dict):
        spec = [spec]
    if not isinstance(spec, list):
        return None

    fallback: Optional[float] = None
    for entry in spec:
        if not isinstance(entry, dict):
            continue
        price_raw = entry.get("price")
        if price_raw is None:
            continue
        try:
            parsed = _parse_price_text(str(price_raw))
        except ValueError:
            continue

        price_type = entry.get("priceType", "")
        if _LIST_PRICE_TYPE not in str(price_type):
            return parsed          # non-list price → return immediately
        if fallback is None:
            fallback = parsed      # store list price as last-resort fallback

    return fallback


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
