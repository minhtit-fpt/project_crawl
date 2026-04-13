"""Tests for parser.py auto-detect functions — extract_price_auto and _extract_from_json_ld."""

import pytest
from scraper.parser import extract_price_auto, _extract_from_json_ld, _extract_from_price_specification

URL = "https://example.com/product/SKU001"


def _page(body: str) -> str:
    return f"<html><head></head><body>{body}</body></html>"


def _json_ld(data: str) -> str:
    return f'<script type="application/ld+json">{data}</script>'


# ── _extract_from_json_ld ──────────────────────────────────────────────────────

class TestExtractFromJsonLd:
    def test_product_with_direct_price(self):
        html = _json_ld('{"@type":"Product","name":"TV","price":5990000}')
        assert _extract_from_json_ld(html) == 5990000.0

    def test_product_with_offers_dict(self):
        html = _json_ld('{"@type":"Product","offers":{"@type":"Offer","price":3500000}}')
        assert _extract_from_json_ld(html) == 3500000.0

    def test_product_with_offers_list(self):
        html = _json_ld('{"@type":"Product","offers":[{"price":1200000},{"price":1100000}]}')
        assert _extract_from_json_ld(html) == 1200000.0  # first offer wins

    def test_price_as_string(self):
        html = _json_ld('{"@type":"Product","price":"6.990.000"}')
        assert _extract_from_json_ld(html) == 6990000.0

    def test_no_json_ld_returns_none(self):
        assert _extract_from_json_ld("<html><body><p>no ld</p></body></html>") is None

    def test_malformed_json_returns_none(self):
        html = '<script type="application/ld+json">{not valid json}</script>'
        assert _extract_from_json_ld(html) is None

    def test_json_ld_without_price_returns_none(self):
        html = _json_ld('{"@type":"Product","name":"TV"}')
        assert _extract_from_json_ld(html) is None

    def test_json_ld_array_at_root(self):
        html = _json_ld('[{"@type":"BreadcrumbList"},{"@type":"Product","price":999000}]')
        assert _extract_from_json_ld(html) == 999000.0

    def test_multiple_json_ld_blocks_uses_first_valid(self):
        html = (
            '<script type="application/ld+json">{"@type":"BreadcrumbList"}</script>'
            '<script type="application/ld+json">{"@type":"Product","price":2200000}</script>'
        )
        assert _extract_from_json_ld(html) == 2200000.0

    # ── priceSpecification support (dieuhoa.vip / dienmaytamanh.vn pattern) ─────

    def test_price_specification_array(self):
        """dieuhoa.vip: priceSpecification list with selling price first."""
        html = _json_ld(
            '{"@type":"Product","priceSpecification":['
            '{"price":"4190000","priceCurrency":"VND"},'
            '{"price":"5500000","priceType":"https://schema.org/ListPrice"}'
            ']}'
        )
        assert _extract_from_json_ld(html) == 4190000.0

    def test_price_specification_prefers_non_list_price(self):
        """ListPrice entry must be skipped when a regular price exists."""
        html = _json_ld(
            '{"@type":"Product","priceSpecification":['
            '{"price":"5500000","priceType":"https://schema.org/ListPrice"},'
            '{"price":"4300000","priceCurrency":"VND"}'
            ']}'
        )
        assert _extract_from_json_ld(html) == 4300000.0

    def test_price_specification_falls_back_to_list_price(self):
        """If all entries have a priceType, return the first parseable one."""
        html = _json_ld(
            '{"@type":"Product","priceSpecification":['
            '{"price":"5500000","priceType":"https://schema.org/ListPrice"}'
            ']}'
        )
        assert _extract_from_json_ld(html) == 5500000.0

    def test_price_specification_single_dict(self):
        html = _json_ld('{"@type":"Product","priceSpecification":{"price":"3990000"}}')
        assert _extract_from_json_ld(html) == 3990000.0

    def test_price_specification_woocommerce_pattern(self):
        """dienmaytamanh.vn: priceSpecification with VND currency."""
        html = _json_ld(
            '{"@type":"Product","priceSpecification":['
            '{"price":"4300000","priceCurrency":"VND"},'
            '{"price":"5250000","priceType":"https://schema.org/ListPrice"}'
            ']}'
        )
        assert _extract_from_json_ld(html) == 4300000.0


# ── extract_price_auto ─────────────────────────────────────────────────────────

class TestExtractPriceAuto:
    def test_detects_via_json_ld(self):
        html = _page(_json_ld('{"@type":"Product","price":5990000}'))
        assert extract_price_auto(html, URL) == 5990000.0

    def test_detects_via_itemprop(self):
        html = _page('<span itemprop="price">3.500.000</span>')
        assert extract_price_auto(html, URL) == 3500000.0

    def test_detects_via_product_price_css(self):
        html = _page('<div class="product-price">2.990.000đ</div>')
        assert extract_price_auto(html, URL) == 2990000.0

    def test_detects_via_price_class(self):
        html = _page('<span class="price">1.200.000</span>')
        assert extract_price_auto(html, URL) == 1200000.0

    def test_json_ld_takes_priority_over_css(self):
        # JSON-LD has 5M, DOM has 3M — JSON-LD wins
        html = _page(
            _json_ld('{"@type":"Product","price":5000000}')
            + '<span class="price">3.000.000</span>'
        )
        assert extract_price_auto(html, URL) == 5000000.0

    def test_returns_none_when_no_price_found(self):
        html = _page("<p>No price here</p>")
        assert extract_price_auto(html, URL) is None

    def test_returns_none_on_empty_html(self):
        assert extract_price_auto("", URL) is None

    def test_skips_selector_that_yields_no_numeric_text(self):
        # .price exists but contains non-parseable text → should continue trying
        html = _page(
            '<span class="price">In stock</span>'
            '<div class="product-price">4.500.000đ</div>'
        )
        result = extract_price_auto(html, URL)
        assert result == 4500000.0

    def test_vnd_format_parsed_correctly(self):
        html = _page('<span itemprop="price">15.990.000đ</span>')
        result = extract_price_auto(html, URL)
        assert result == 15990000.0

    def test_does_not_raise_on_bad_html(self):
        # Should return None, never raise
        result = extract_price_auto("<<<not html>>>", URL)
        assert result is None

    def test_detects_via_price_ins_span_woocommerce(self):
        """dienmaytamanh.vn: WooCommerce .price ins span (sale price only)."""
        html = _page(
            '<p class="price">'
            '<del><span>5.250.000₫</span></del>'
            '<ins><span>4.300.000₫</span></ins>'
            '</p>'
        )
        assert extract_price_auto(html, URL) == 4300000.0

    def test_detects_via_item_price(self):
        """muahangtaikho.vn: .item-price selector."""
        html = _page('<p class="item-price">4.150.000đ</p>')
        assert extract_price_auto(html, URL) == 4150000.0

    def test_detects_via_price_main_bem(self):
        """dienmayan.vn: BEM class containing 'price__main__1'."""
        html = _page(
            '<span class="w66-productdetail__anhgia__two__price__main__1">'
            '15.990.000đ'
            '</span>'
        )
        assert extract_price_auto(html, URL) == 15990000.0

    def test_price_ins_span_takes_priority_over_plain_price(self):
        """WooCommerce: .price ins span must win over generic .price container."""
        html = _page(
            '<p class="price">'
            '<del><span>5.250.000₫</span></del>'
            '<ins><span>4.300.000₫</span></ins>'
            '</p>'
        )
        result = extract_price_auto(html, URL)
        # Must be the sale price, not the garbled "5.250.0004.300.000" from .price
        assert result == 4300000.0


# ── CrawlJob selectors=None integration check ─────────────────────────────────

class TestCrawlJobSelectorsOptional:
    def test_crawl_job_accepts_none_selectors(self):
        from scraper.scheduler import CrawlJob
        job = CrawlJob(
            site_name="TestSite",
            url="https://example.com/p/SKU1",
            sku="SKU1",
            requires_js=False,
            rate_limit_seconds=1.0,
            selectors=None,
        )
        assert job.selectors is None

    def test_crawl_job_selectors_default_is_none(self):
        from scraper.scheduler import CrawlJob
        job = CrawlJob(
            site_name="TestSite",
            url="https://example.com/p/SKU1",
            sku="SKU1",
            requires_js=False,
            rate_limit_seconds=1.0,
        )
        assert job.selectors is None
