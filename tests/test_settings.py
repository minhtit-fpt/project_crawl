"""Tests for scraper/config/settings.py — load_sites() YAML validation."""

import textwrap
import pytest
from pathlib import Path

from scraper.config.settings import load_sites, SiteConfig, SelectorConfig


@pytest.fixture()
def yaml_file(tmp_path: Path):
    """Factory: write YAML content to a temp file and return its path."""
    def _write(content: str) -> str:
        p = tmp_path / "sites.yaml"
        p.write_text(textwrap.dedent(content), encoding="utf-8")
        return str(p)
    return _write


VALID_YAML = """
    sites:
      - name: "TestStore"
        base_url: "https://example.com/product/{sku}"
        skus:
          - "SKU-001"
          - "SKU-002"
        selectors:
          price_css: "span.price"
          price_xpath: "//span[@class='price']"
        requires_js: false
        rate_limit_seconds: 2
"""


class TestLoadSitesSuccess:
    def test_returns_list_of_site_configs(self, yaml_file):
        sites = load_sites(yaml_file(VALID_YAML))
        assert isinstance(sites, list)
        assert len(sites) == 1

    def test_site_fields_parsed_correctly(self, yaml_file):
        site = load_sites(yaml_file(VALID_YAML))[0]
        assert site.name == "TestStore"
        assert site.base_url == "https://example.com/product/{sku}"
        assert site.skus == ["SKU-001", "SKU-002"]
        assert site.requires_js is False
        assert site.rate_limit_seconds == 2.0

    def test_selectors_parsed(self, yaml_file):
        site = load_sites(yaml_file(VALID_YAML))[0]
        assert site.selectors.price_css == "span.price"
        assert site.selectors.price_xpath == "//span[@class='price']"

    def test_site_config_is_frozen(self, yaml_file):
        site = load_sites(yaml_file(VALID_YAML))[0]
        with pytest.raises((AttributeError, TypeError)):
            site.name = "changed"  # type: ignore

    def test_multiple_sites(self, yaml_file):
        yaml = """
            sites:
              - name: "Site1"
                base_url: "https://site1.com/{sku}"
                skus: ["A"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                requires_js: false
                rate_limit_seconds: 1
              - name: "Site2"
                base_url: "https://site2.com/{sku}"
                skus: ["B", "C"]
                selectors:
                  price_css: ".q"
                  price_xpath: "//q"
                requires_js: true
                rate_limit_seconds: 3
        """
        sites = load_sites(yaml_file(yaml))
        assert len(sites) == 2
        assert sites[1].requires_js is True
        assert len(sites[1].skus) == 2

    def test_requires_js_defaults_to_false(self, yaml_file):
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                rate_limit_seconds: 1
        """
        site = load_sites(yaml_file(yaml))[0]
        assert site.requires_js is False

    def test_rate_limit_defaults_to_1(self, yaml_file):
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                requires_js: false
        """
        site = load_sites(yaml_file(yaml))[0]
        assert site.rate_limit_seconds == 1.0


class TestLoadSitesErrors:
    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_sites("/nonexistent/path/sites.yaml")

    def test_missing_sites_key_raises(self, yaml_file):
        with pytest.raises(ValueError, match="'sites'"):
            load_sites(yaml_file("other_key: []"))

    def test_missing_sku_placeholder_raises(self, yaml_file):
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/no-placeholder"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="{sku}"):
            load_sites(yaml_file(yaml))

    def test_empty_skus_raises(self, yaml_file):
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: []
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="empty"):
            load_sites(yaml_file(yaml))

    def test_invalid_url_scheme_raises(self, yaml_file):
        yaml = """
            sites:
              - name: "S"
                base_url: "ftp://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="http"):
            load_sites(yaml_file(yaml))

    def test_missing_name_raises(self, yaml_file):
        yaml = """
            sites:
              - base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="name"):
            load_sites(yaml_file(yaml))

    def test_missing_selectors_raises(self, yaml_file):
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError):
            load_sites(yaml_file(yaml))

    def test_invalid_sku_mode_raises(self, yaml_file):
        """Line 133: sku_mode not in ('direct', 'search')."""
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                sku_mode: "invalid"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="sku_mode"):
            load_sites(yaml_file(yaml))

    def test_base_url_no_netloc_raises(self, yaml_file):
        """Line 199: URL with scheme but no host (triple-slash path, empty netloc)."""
        yaml = """
            sites:
              - name: "S"
                base_url: "https:///{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError):
            load_sites(yaml_file(yaml))


class TestSearchModeSettings:
    def test_search_mode_parsed_correctly(self, yaml_file):
        """Lines 141-150: sku_mode='search' requires search_url and search_selectors."""
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                sku_mode: "search"
                search_url: "https://x.com/search?q={sku}"
                search_selectors:
                  result_link_css: "a.result"
                requires_js: false
                rate_limit_seconds: 1
        """
        site = load_sites(yaml_file(yaml))[0]
        assert site.sku_mode == "search"
        assert site.search_url == "https://x.com/search?q={sku}"
        assert site.search_selectors is not None
        assert site.search_selectors.result_link_css == "a.result"

    def test_search_mode_missing_search_url_raises(self, yaml_file):
        """Lines 142-143: search_url required when sku_mode='search'."""
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                sku_mode: "search"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="search_url"):
            load_sites(yaml_file(yaml))

    def test_search_mode_search_url_missing_sku_placeholder_raises(self, yaml_file):
        """Lines 144-145: search_url must contain {sku}."""
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                sku_mode: "search"
                search_url: "https://x.com/search"
                search_selectors:
                  result_link_css: "a.result"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="{sku}"):
            load_sites(yaml_file(yaml))

    def test_search_mode_missing_search_selectors_raises(self, yaml_file):
        """Lines 148-149: search_selectors required when sku_mode='search'."""
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                sku_mode: "search"
                search_url: "https://x.com/search?q={sku}"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="search_selectors"):
            load_sites(yaml_file(yaml))

    def test_search_selectors_not_dict_raises(self, yaml_file):
        """Lines 174-175: _parse_search_selectors requires a mapping."""
        yaml = """
            sites:
              - name: "S"
                base_url: "https://x.com/{sku}"
                skus: ["X"]
                selectors:
                  price_css: ".p"
                  price_xpath: "//p"
                sku_mode: "search"
                search_url: "https://x.com/search?q={sku}"
                search_selectors: "not-a-dict"
                requires_js: false
                rate_limit_seconds: 1
        """
        with pytest.raises(ValueError, match="result_link_css"):
            load_sites(yaml_file(yaml))
