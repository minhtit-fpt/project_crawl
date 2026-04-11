"""Tests for scraper/resolver.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from scraper.config.settings import SearchSelectors, SelectorConfig, SiteConfig
from scraper.resolver import _make_absolute, resolve_sku_to_url


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_site(sku_mode: str = "direct", **kwargs) -> SiteConfig:
    defaults = dict(
        name="TestSite",
        base_url="https://example.com/product/{sku}",
        skus=["SKU-001"],
        selectors=SelectorConfig(price_css=".price", price_xpath="//span"),
        requires_js=False,
        rate_limit_seconds=1.0,
        sku_mode=sku_mode,
        search_url=None,
        search_selectors=None,
    )
    defaults.update(kwargs)
    return SiteConfig(**defaults)


def _make_search_site(search_html: str = "", result_link_css: str = "a.result") -> SiteConfig:
    return _make_site(
        sku_mode="search",
        search_url="https://example.com/search?q={sku}",
        search_selectors=SearchSelectors(result_link_css=result_link_css),
    )


# ── Direct mode ────────────────────────────────────────────────────────────────

class TestDirectMode:
    def test_substitutes_sku_into_base_url(self):
        site = _make_site(sku_mode="direct")
        url = resolve_sku_to_url("SKU-001", site)
        assert url == "https://example.com/product/SKU-001"

    def test_preserves_rest_of_url(self):
        site = _make_site(
            sku_mode="direct",
            base_url="https://shop.vn/category/{sku}?ref=home",
        )
        url = resolve_sku_to_url("ABC123", site)
        assert url == "https://shop.vn/category/ABC123?ref=home"


# ── Search mode ────────────────────────────────────────────────────────────────

SEARCH_HTML_WITH_RESULTS = """
<html><body>
  <div class="results">
    <a class="result" href="/product/full-slug-nis-c09r2t28">Máy lạnh Nagakawa</a>
    <a class="result" href="/product/other-product">Other</a>
  </div>
</body></html>
"""

SEARCH_HTML_EMPTY = "<html><body><p>Không tìm thấy sản phẩm</p></body></html>"


class TestSearchMode:
    def _mock_response(self, html: str, status: int = 200):
        mock = MagicMock()
        mock.status_code = status
        mock.text = html
        mock.raise_for_status = MagicMock()
        return mock

    @patch("scraper.resolver.requests.get")
    def test_returns_first_result_link(self, mock_get):
        mock_get.return_value = self._mock_response(SEARCH_HTML_WITH_RESULTS)
        site = _make_search_site()
        url = resolve_sku_to_url("NIS-C09R2T28", site)
        assert url == "https://example.com/product/full-slug-nis-c09r2t28"

    @patch("scraper.resolver.requests.get")
    def test_builds_correct_search_url(self, mock_get):
        mock_get.return_value = self._mock_response(SEARCH_HTML_WITH_RESULTS)
        site = _make_search_site()
        resolve_sku_to_url("NIS-C09R2T28", site)
        called_url = mock_get.call_args[0][0]
        assert "NIS-C09R2T28" in called_url

    @patch("scraper.resolver.requests.get")
    def test_returns_none_when_no_results(self, mock_get):
        mock_get.return_value = self._mock_response(SEARCH_HTML_EMPTY)
        site = _make_search_site()
        url = resolve_sku_to_url("NOTFOUND", site)
        assert url is None

    @patch("scraper.resolver.requests.get")
    def test_returns_none_on_network_error(self, mock_get):
        import requests as req
        mock_get.side_effect = req.RequestException("timeout")
        site = _make_search_site()
        url = resolve_sku_to_url("NIS-C09R2T28", site)
        assert url is None

    @patch("scraper.resolver.requests.get")
    def test_returns_none_when_search_url_missing(self, mock_get):
        site = _make_site(sku_mode="search")  # no search_url
        url = resolve_sku_to_url("NIS-C09R2T28", site)
        mock_get.assert_not_called()
        assert url is None

    @patch("scraper.resolver.requests.get")
    def test_absolute_href_returned_unchanged(self, mock_get):
        html = '<a class="result" href="https://other.com/product/abc">Product</a>'
        mock_get.return_value = self._mock_response(html)
        site = _make_search_site()
        url = resolve_sku_to_url("ABC", site)
        assert url == "https://other.com/product/abc"


# ── _make_absolute helper ──────────────────────────────────────────────────────

class TestUnknownSkuMode:
    def test_unknown_sku_mode_falls_back_to_direct(self):
        """Lines 55-56: unknown sku_mode logs error and falls back to direct substitution."""
        site = _make_site(sku_mode="unknown_mode")
        url = resolve_sku_to_url("SKU-X", site)
        # Falls back to base_url.replace("{sku}", sku)
        assert url == "https://example.com/product/SKU-X"


class TestDumpDebugHtml:
    @patch("scraper.resolver.requests.get")
    def test_oserror_on_debug_dump_is_swallowed(self, mock_get):
        """Lines 114-115: OSError when writing debug HTML is caught silently."""
        from unittest.mock import patch as patch2

        mock_get.return_value = MagicMock(
            status_code=200,
            text=SEARCH_HTML_EMPTY,
            raise_for_status=MagicMock(),
        )
        site = _make_search_site()

        # Patch open() inside resolver to raise OSError
        with patch2("builtins.open", side_effect=OSError("disk full")):
            # Should not raise — OSError is caught and logged
            url = resolve_sku_to_url("NOTFOUND", site)

        assert url is None


class TestMakeAbsolute:
    def test_relative_path_becomes_absolute(self):
        result = _make_absolute("/may-lanh/product-slug", "https://www.dienmayxanh.com/may-lanh/{sku}")
        assert result == "https://www.dienmayxanh.com/may-lanh/product-slug"

    def test_absolute_href_unchanged(self):
        result = _make_absolute("https://example.com/product", "https://other.com/x/{sku}")
        assert result == "https://example.com/product"

    def test_path_without_leading_slash(self):
        result = _make_absolute("product/slug", "https://example.com/shop/{sku}")
        assert result == "https://example.com/product/slug"
