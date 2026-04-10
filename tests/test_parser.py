"""Tests for scraper/parser.py — price extraction and text parsing."""

import pytest
from scraper.parser import extract_price, _parse_price_text, _strip_html_tags


# ── Fake response helpers ──────────────────────────────────────────────────────

class FakeSelector:
    def __init__(self, text: str | None):
        self._text = text

    def get(self):
        return self._text

    def getall(self):
        return [self._text] if self._text else []


class FakeResponse:
    """Minimal fake Scrapy response for testing extract_price."""

    def __init__(self, css_text: str | None = None, xpath_text: str | None = None):
        self._css_text = css_text
        self._xpath_text = xpath_text

    def css(self, query: str) -> FakeSelector:
        return FakeSelector(self._css_text)

    def xpath(self, query: str) -> FakeSelector:
        return FakeSelector(self._xpath_text)


class FakeSelectors:
    price_css = "span.price"
    price_xpath = "//span[@class='price']"


# ── extract_price ──────────────────────────────────────────────────────────────

class TestExtractPrice:
    def test_returns_float_from_css(self):
        resp = FakeResponse(css_text="$99.99")
        result = extract_price(resp, FakeSelectors())
        assert result == 99.99

    def test_falls_back_to_xpath_when_css_empty(self):
        resp = FakeResponse(css_text=None, xpath_text="49.00")
        result = extract_price(resp, FakeSelectors())
        assert result == 49.0

    def test_returns_none_when_both_selectors_empty(self):
        resp = FakeResponse(css_text=None, xpath_text=None)
        result = extract_price(resp, FakeSelectors())
        assert result is None

    def test_returns_none_when_both_selectors_whitespace(self):
        resp = FakeResponse(css_text="   ", xpath_text="  ")
        result = extract_price(resp, FakeSelectors())
        assert result is None

    def test_raises_value_error_on_unparseable_text(self):
        resp = FakeResponse(css_text="out of stock")
        with pytest.raises(ValueError):
            extract_price(resp, FakeSelectors())

    def test_css_raises_gracefully_falls_to_xpath(self):
        """If CSS selector itself throws, should fall back to XPath."""
        class BrokenCSSResponse:
            def css(self, q):
                raise RuntimeError("selector error")
            def xpath(self, q):
                return FakeSelector("25.00")

        result = extract_price(BrokenCSSResponse(), FakeSelectors())
        assert result == 25.0

    def test_html_tags_stripped_from_css_result(self):
        resp = FakeResponse(css_text="<span>$1,299.00</span>")
        result = extract_price(resp, FakeSelectors())
        assert result == 1299.0


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
