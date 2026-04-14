"""Tests for scraper/config/api_source.py — CMS API fetching and job building."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from scraper.config.api_source import (
    ApiProduct,
    ApiSourceError,
    build_jobs_from_api,
    fetch_products,
    _domain_from_url,
    _extract_items,
    _extract_total_pages,
)
from scraper.scheduler import CrawlJob

API_URL = "https://cms.example.com/wp-json/v1/crawler"
API_TOKEN = "test-token-123"

# ── Helpers ────────────────────────────────────────────────────────────────────

def _mock_response(json_data: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


def _single_page_response(items: list[dict], total_pages: int = 1) -> dict:
    return {"data": items, "totals": len(items), "totalPages": total_pages}


SAMPLE_ITEM = {
    "title": "Điều Hòa Casper 9000btu",
    "sku": "SC-09FB36A",
    "crawler": [
        "https://site-a.com/dieu-hoa/casper/sc-09fb36a",
        "https://site-b.com/may-lanh-casper-sc-09fb36a",
    ],
}


# ── fetch_products — happy paths ───────────────────────────────────────────────

class TestFetchProductsSinglePage:
    def test_returns_list_of_api_products(self):
        resp = _mock_response(_single_page_response([SAMPLE_ITEM]))
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            products = fetch_products(API_URL, API_TOKEN)
        assert len(products) == 1
        assert isinstance(products[0], ApiProduct)

    def test_product_fields_mapped_correctly(self):
        resp = _mock_response(_single_page_response([SAMPLE_ITEM]))
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            products = fetch_products(API_URL, API_TOKEN)
        p = products[0]
        assert p.sku == "SC-09FB36A"
        assert p.title == "Điều Hòa Casper 9000btu"
        assert p.crawler_urls == SAMPLE_ITEM["crawler"]

    def test_token_sent_as_header(self):
        resp = _mock_response(_single_page_response([SAMPLE_ITEM]))
        with patch("scraper.config.api_source.requests.get", return_value=resp) as mock_get:
            fetch_products(API_URL, API_TOKEN)
        _, kwargs = mock_get.call_args
        assert kwargs["headers"]["token"] == API_TOKEN

    def test_empty_data_returns_empty_list(self):
        resp = _mock_response(_single_page_response([]))
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            products = fetch_products(API_URL, API_TOKEN)
        assert products == []

    def test_multiple_products_single_page(self):
        items = [
            {"title": "A", "sku": "SKU-A", "crawler": ["https://a.com/a"]},
            {"title": "B", "sku": "SKU-B", "crawler": ["https://b.com/b"]},
            {"title": "C", "sku": "SKU-C", "crawler": ["https://c.com/c"]},
        ]
        resp = _mock_response(_single_page_response(items))
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            products = fetch_products(API_URL, API_TOKEN)
        assert len(products) == 3
        assert [p.sku for p in products] == ["SKU-A", "SKU-B", "SKU-C"]


class TestFetchProductsPagination:
    def test_fetches_all_pages(self):
        page1 = {"data": [{"title": "A", "sku": "SKU-A", "crawler": ["https://a.com"]}], "totalPages": 3}
        page2 = {"data": [{"title": "B", "sku": "SKU-B", "crawler": ["https://b.com"]}], "totalPages": 3}
        page3 = {"data": [{"title": "C", "sku": "SKU-C", "crawler": ["https://c.com"]}], "totalPages": 3}

        responses = [
            _mock_response(page1),
            _mock_response(page2),
            _mock_response(page3),
        ]
        with patch("scraper.config.api_source.requests.get", side_effect=responses):
            products = fetch_products(API_URL, API_TOKEN)

        assert len(products) == 3
        assert {p.sku for p in products} == {"SKU-A", "SKU-B", "SKU-C"}

    def test_page_param_sent_from_page_2(self):
        page1 = {"data": [{"title": "A", "sku": "SKU-A", "crawler": ["https://a.com"]}], "totalPages": 2}
        page2 = {"data": [{"title": "B", "sku": "SKU-B", "crawler": ["https://b.com"]}], "totalPages": 2}

        call_params = []

        def capture_get(url, headers, params, timeout):
            call_params.append(params)
            return _mock_response(page1 if not call_params or len(call_params) == 1 else page2)

        with patch("scraper.config.api_source.requests.get", side_effect=capture_get):
            fetch_products(API_URL, API_TOKEN)

        assert call_params[0] == {}       # page 1 — no ?page= param
        assert call_params[1] == {"page": 2}

    def test_single_page_makes_one_request(self):
        resp = _mock_response(_single_page_response([SAMPLE_ITEM]))
        with patch("scraper.config.api_source.requests.get", return_value=resp) as mock_get:
            fetch_products(API_URL, API_TOKEN)
        assert mock_get.call_count == 1


# ── fetch_products — error handling ───────────────────────────────────────────

class TestFetchProductsErrors:
    def test_connection_error_raises_api_source_error(self):
        with patch("scraper.config.api_source.requests.get",
                   side_effect=requests.ConnectionError("refused")):
            with pytest.raises(ApiSourceError, match="Cannot connect"):
                fetch_products(API_URL, API_TOKEN)

    def test_timeout_raises_api_source_error(self):
        with patch("scraper.config.api_source.requests.get",
                   side_effect=requests.Timeout()):
            with pytest.raises(ApiSourceError, match="timed out"):
                fetch_products(API_URL, API_TOKEN)

    def test_401_raises_api_source_error(self):
        resp = _mock_response({}, status_code=401)
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            with pytest.raises(ApiSourceError, match="authentication failed"):
                fetch_products(API_URL, API_TOKEN)

    def test_403_raises_api_source_error(self):
        resp = _mock_response({}, status_code=403)
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            with pytest.raises(ApiSourceError, match="authentication failed"):
                fetch_products(API_URL, API_TOKEN)

    def test_500_raises_api_source_error(self):
        resp = _mock_response({}, status_code=500)
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            with pytest.raises(ApiSourceError, match="unexpected status 500"):
                fetch_products(API_URL, API_TOKEN)

    def test_invalid_json_raises_api_source_error(self):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.side_effect = ValueError("No JSON")
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            with pytest.raises(ApiSourceError, match="not valid JSON"):
                fetch_products(API_URL, API_TOKEN)

    def test_missing_data_key_raises_api_source_error(self):
        resp = _mock_response({"totals": 0, "totalPages": 1})
        with patch("scraper.config.api_source.requests.get", return_value=resp):
            with pytest.raises(ApiSourceError, match="missing required 'data' key"):
                fetch_products(API_URL, API_TOKEN)


# ── _extract_items — item-level validation ────────────────────────────────────

class TestExtractItems:
    def test_skips_item_with_missing_sku(self):
        data = {"data": [{"title": "No SKU", "crawler": ["https://x.com"]}], "totalPages": 1}
        items = _extract_items(data, page=1)
        assert items == []

    def test_skips_item_with_non_string_sku(self):
        data = {"data": [{"sku": 12345, "crawler": ["https://x.com"]}], "totalPages": 1}
        items = _extract_items(data, page=1)
        assert items == []

    def test_skips_non_dict_items(self):
        data = {"data": ["not-a-dict", None, 42], "totalPages": 1}
        items = _extract_items(data, page=1)
        assert items == []

    def test_skips_blank_urls_in_crawler(self):
        data = {"data": [{"sku": "S1", "crawler": ["", "  ", "https://ok.com"]}], "totalPages": 1}
        items = _extract_items(data, page=1)
        assert items[0].crawler_urls == ["https://ok.com"]

    def test_empty_crawler_list_is_allowed(self):
        data = {"data": [{"sku": "S1", "crawler": []}], "totalPages": 1}
        items = _extract_items(data, page=1)
        assert len(items) == 1
        assert items[0].crawler_urls == []

    def test_title_defaults_to_empty_string_when_absent(self):
        data = {"data": [{"sku": "S1", "crawler": ["https://x.com"]}], "totalPages": 1}
        items = _extract_items(data, page=1)
        assert items[0].title == ""


# ── build_jobs_from_api ────────────────────────────────────────────────────────

class TestBuildJobsFromApi:
    def test_one_job_per_crawler_url(self):
        products = [
            ApiProduct(title="TV", sku="TV-001", crawler_urls=["https://a.com/tv", "https://b.com/tv"]),
        ]
        jobs = build_jobs_from_api(products)
        assert len(jobs) == 2

    def test_jobs_have_correct_sku_and_url(self):
        products = [
            ApiProduct(title="TV", sku="TV-001", crawler_urls=["https://a.com/tv"]),
        ]
        jobs = build_jobs_from_api(products)
        assert jobs[0].sku == "TV-001"
        assert jobs[0].url == "https://a.com/tv"

    def test_jobs_have_no_selectors(self):
        products = [ApiProduct(title="AC", sku="AC-001", crawler_urls=["https://x.com/ac"])]
        jobs = build_jobs_from_api(products)
        assert jobs[0].selectors is None

    def test_site_name_is_domain(self):
        products = [ApiProduct(title="AC", sku="AC-001", crawler_urls=["https://mediamart.vn/product/abc"])]
        jobs = build_jobs_from_api(products)
        assert jobs[0].site_name == "mediamart.vn"

    def test_skips_products_with_no_urls(self):
        products = [
            ApiProduct(title="A", sku="SKU-A", crawler_urls=[]),
            ApiProduct(title="B", sku="SKU-B", crawler_urls=["https://b.com"]),
        ]
        jobs = build_jobs_from_api(products)
        assert len(jobs) == 1
        assert jobs[0].sku == "SKU-B"

    def test_empty_products_returns_empty_jobs(self):
        assert build_jobs_from_api([]) == []

    def test_multiple_products_multiple_urls(self):
        products = [
            ApiProduct(title="A", sku="SKU-A", crawler_urls=["https://a.com", "https://b.com"]),
            ApiProduct(title="B", sku="SKU-B", crawler_urls=["https://c.com"]),
        ]
        jobs = build_jobs_from_api(products)
        assert len(jobs) == 3
        skus = [j.sku for j in jobs]
        assert skus.count("SKU-A") == 2
        assert skus.count("SKU-B") == 1

    def test_all_jobs_are_crawl_job_instances(self):
        products = [ApiProduct(title="X", sku="X-1", crawler_urls=["https://x.com"])]
        jobs = build_jobs_from_api(products)
        assert all(isinstance(j, CrawlJob) for j in jobs)


# ── _domain_from_url ───────────────────────────────────────────────────────────

class TestDomainFromUrl:
    def test_extracts_domain(self):
        assert _domain_from_url("https://www.dienmayxanh.com/product/123") == "www.dienmayxanh.com"

    def test_extracts_domain_without_www(self):
        assert _domain_from_url("https://mediamart.vn/abc") == "mediamart.vn"

    def test_returns_url_on_parse_failure(self):
        assert _domain_from_url("not-a-url") == "not-a-url"


# ── _extract_total_pages ───────────────────────────────────────────────────────

class TestExtractTotalPages:
    def test_returns_total_pages(self):
        assert _extract_total_pages({"data": [], "totalPages": 5}) == 5

    def test_defaults_to_1_when_missing(self):
        assert _extract_total_pages({"data": []}) == 1

    def test_defaults_to_1_on_invalid_value(self):
        assert _extract_total_pages({"data": [], "totalPages": "bad"}) == 1

    def test_raises_on_missing_data_key(self):
        with pytest.raises(ApiSourceError):
            _extract_total_pages({"totalPages": 1})
