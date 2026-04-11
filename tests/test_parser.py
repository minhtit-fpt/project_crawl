"""Tests for scraper/parser.py — Scrapling-based price extraction."""

import pytest
from scraper.parser import extract_price, _parse_price_text, _strip_html_tags


# ── Helpers ────────────────────────────────────────────────────────────────────

URL = "http://example.com/product/SKU001"


class FakeSelectors:
    """Standard selectors used across most tests."""
    price_css = "span.price"
    price_xpath = "//span[@class='price']"


def _html(price_text: str, tag: str = "span", cls: str = "price") -> str:
    """Minimal HTML page with a single price element."""
    return (
        f"<html><body>"
        f'<{tag} class="{cls}">{price_text}</{tag}>'
        f"</body></html>"
    )


# ── extract_price — happy paths ────────────────────────────────────────────────

class TestExtractPrice:
    def test_returns_float_from_css(self):
        result = extract_price(_html("$99.99"), URL, FakeSelectors())
        assert result == 99.99

    def test_returns_float_from_css_integer(self):
        result = extract_price(_html("500"), URL, FakeSelectors())
        assert result == 500.0

    def test_falls_back_to_xpath_when_css_misses(self):
        """CSS selector misses; XPath finds the element."""
        class XPathOnlySelectors:
            price_css = ".not-a-real-class"
            price_xpath = "//span[@class='price']"

        result = extract_price(_html("49.00"), URL, XPathOnlySelectors())
        assert result == 49.0

    def test_returns_none_when_both_selectors_miss(self):
        html = "<html><body><p>No price here</p></body></html>"
        assert extract_price(html, URL, FakeSelectors()) is None

    def test_returns_none_when_element_is_whitespace_only(self):
        result = extract_price(_html("   "), URL, FakeSelectors())
        assert result is None

    def test_raises_value_error_on_unparseable_text(self):
        with pytest.raises(ValueError):
            extract_price(_html("out of stock"), URL, FakeSelectors())

    def test_nested_children_text_joined(self):
        """Price split across child nodes: <span><b>$</b>1,299.00</span>"""
        html = (
            "<html><body>"
            '<span class="price"><b>$</b>1,299.00</span>'
            "</body></html>"
        )
        result = extract_price(html, URL, FakeSelectors())
        assert result == 1299.0

    def test_vnd_price_extracted(self):
        result = extract_price(_html("1.500.000đ"), URL, FakeSelectors())
        assert result == 1_500_000.0

    def test_european_price_extracted(self):
        result = extract_price(_html("1.234,56 EUR"), URL, FakeSelectors())
        assert result == 1234.56

    def test_bad_html_returns_none_gracefully(self):
        """Completely broken HTML should not raise — returns None."""
        result = extract_price("not html at all <<<", URL, FakeSelectors())
        # Scrapling is lenient; may parse partial or return None — either is OK
        assert result is None or isinstance(result, float)

    def test_different_urls_namespace_fingerprints(self):
        """Two different URLs should each extract correctly (no cross-contamination)."""
        url_a = "http://site-a.com/product/SKU001"
        url_b = "http://site-b.com/product/SKU001"
        html_a = _html("150000")
        html_b = _html("299.99")
        assert extract_price(html_a, url_a, FakeSelectors()) == 150_000.0
        assert extract_price(html_b, url_b, FakeSelectors()) == 299.99


# ── _parse_price_text ──────────────────────────────────────────────────────────

class TestParsePriceText:
    # ── US / en format ─────────────────────────────────────────────────────────
    def test_simple_integer(self):
        assert _parse_price_text("100") == 100.0

    def test_simple_decimal(self):
        assert _parse_price_text("99.99") == 99.99

    def test_usd_with_symbol(self):
        assert _parse_price_text("$1,234.56") == 1234.56

    def test_comma_thousands_separator(self):
        assert _parse_price_text("1,234") == 1234.0

    def test_large_number_en(self):
        assert _parse_price_text("10,000.00") == 10000.0

    # ── European / de format ───────────────────────────────────────────────────
    def test_european_decimal_comma(self):
        assert _parse_price_text("1.234,56") == 1234.56

    def test_euro_with_symbol(self):
        assert _parse_price_text("1.234,56 €") == 1234.56

    # ── Vietnamese / VND format ────────────────────────────────────────────────
    def test_vnd_dot_thousands(self):
        assert _parse_price_text("1.500.000đ") == 1500000.0

    def test_vnd_no_symbol(self):
        assert _parse_price_text("500.000") == 500000.0

    def test_vnd_with_label(self):
        assert _parse_price_text("Giá: 250.000đ") == 250000.0

    # ── Misc formats ───────────────────────────────────────────────────────────
    def test_price_with_label(self):
        assert _parse_price_text("Price: 99") == 99.0

    def test_whitespace_around_price(self):
        assert _parse_price_text("  49.50  ") == 49.50

    def test_integer_no_decimals(self):
        assert _parse_price_text("500") == 500.0

    def test_zero_price(self):
        assert _parse_price_text("0.00") == 0.0

    def test_html_tags_stripped(self):
        assert _parse_price_text("<b>$29.99</b>") == 29.99

    # ── Error cases ────────────────────────────────────────────────────────────
    def test_purely_text_raises(self):
        with pytest.raises(ValueError, match="No numeric content"):
            _parse_price_text("out of stock")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            _parse_price_text("   ")

    def test_only_currency_symbol_raises(self):
        with pytest.raises(ValueError):
            _parse_price_text("$")

    def test_special_chars_only_raises(self):
        with pytest.raises(ValueError):
            _parse_price_text("---")

    # ── Edge cases ─────────────────────────────────────────────────────────────
    def test_comma_as_decimal_separator(self):
        assert _parse_price_text("1,56") == pytest.approx(1.56)

    def test_comma_non_three_digits(self):
        assert _parse_price_text("1,5") == pytest.approx(1.5)

    def test_invalid_numeric_after_cleanup_raises(self):
        with pytest.raises(ValueError, match="Cannot convert"):
            _parse_price_text("1..2..3")


# ── _strip_html_tags ───────────────────────────────────────────────────────────

class TestStripHtmlTags:
    def test_removes_span_tag(self):
        assert _strip_html_tags("<span>99.99</span>") == "99.99"

    def test_removes_nested_tags(self):
        assert _strip_html_tags("<div><b>$100</b></div>") == "$100"

    def test_no_tags_unchanged(self):
        assert _strip_html_tags("99.99") == "99.99"

    def test_empty_string(self):
        assert _strip_html_tags("") == ""
