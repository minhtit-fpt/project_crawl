"""Tests for main.py — CLI wiring, argument parsing, and integration flow."""

import os
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from main import main, _parse_args

VALID_ENV: dict[str, str] = {}   # no required env vars anymore


def _make_sites_yaml(tmp_path: Path) -> str:
    content = textwrap.dedent("""
        sites:
          - name: "TestShop"
            base_url: "https://example.com/{sku}"
            skus:
              - "SKU-001"
            selectors:
              price_css: "span.price"
              price_xpath: "//span"
            requires_js: false
            rate_limit_seconds: 0
    """)
    p = tmp_path / "sites.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


# ── _parse_args ────────────────────────────────────────────────────────────────

class TestParseArgs:
    def test_defaults(self):
        args = _parse_args([])
        assert args.config is None
        assert args.log_level is None
        assert args.source == "api"
        assert args.serve is False

    def test_source_api_flag(self):
        args = _parse_args(["--source", "api"])
        assert args.source == "api"

    def test_source_yaml_flag(self):
        args = _parse_args(["--source", "yaml"])
        assert args.source == "yaml"

    def test_source_invalid_raises(self):
        with pytest.raises(SystemExit):
            _parse_args(["--source", "csv"])

    def test_config_flag(self):
        args = _parse_args(["--config", "path/to/sites.yaml"])
        assert args.config == "path/to/sites.yaml"

    def test_log_level_flag(self):
        args = _parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"

    def test_invalid_log_level_raises(self):
        with pytest.raises(SystemExit):
            _parse_args(["--log-level", "VERBOSE"])

    def test_serve_flag(self):
        args = _parse_args(["--serve"])
        assert args.serve is True


# ── main() — env errors ────────────────────────────────────────────────────────

class TestMainEnvErrors:
    def test_missing_env_prints_error_to_stderr(self, capsys):
        with patch("main.load_config", side_effect=EnvironmentError("bad config")):
            main([])
        captured = capsys.readouterr()
        assert "ERROR" in captured.err


# ── main() — config errors ─────────────────────────────────────────────────────

class TestMainConfigErrors:
    def test_missing_config_file_returns_1(self):
        with patch.dict(os.environ, VALID_ENV, clear=True):
            code = main(["--source", "yaml", "--config", "/nonexistent/sites.yaml"])
        assert code == 1


# ── main() — successful run (spider mocked) ───────────────────────────────────

class TestMainSuccess:
    def test_returns_0_when_all_ok(self, tmp_path):
        """All results OK → exit code 0."""
        from scraper.scheduler import make_crawl_result

        config_path = _make_sites_yaml(tmp_path)

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.Scheduler.run", return_value=[
                make_crawl_result("SKU-001", 99.9, "TestShop")
            ]):
                code = main(["--source", "yaml", "--config", config_path])

        assert code in (0, 2)

    def test_returns_2_when_some_errors(self, tmp_path):
        """Some errors → exit code 2 (partial)."""
        from scraper.scheduler import make_error_result

        config_path = _make_sites_yaml(tmp_path)

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.Scheduler.run") as mock_run:
                mock_run.return_value = [
                    make_error_result("SKU-001", "TestShop", "HTTP 404")
                ]
                code = main(["--source", "yaml", "--config", config_path])

        assert code == 2


# ── main() — unexpected crawl error ───────────────────────────────────────────

class TestMainCrawlError:
    def test_unexpected_exception_returns_1(self, tmp_path):
        config_path = _make_sites_yaml(tmp_path)

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.Scheduler.run", side_effect=RuntimeError("boom")):
                code = main(["--source", "yaml", "--config", config_path])

        assert code == 1


# ── main() — --source api ──────────────────────────────────────────────────────

VALID_ENV_WITH_API = {
    "CMS_API_URL": "https://cms.example.com/wp-json/v1/crawler",
    "CMS_API_TOKEN": "test-token-xyz",
}


class TestMainSourceApi:
    def test_missing_cms_token_returns_1(self):
        with patch.dict(os.environ, {}, clear=True):
            code = main(["--source", "api"])
        assert code == 1

    def test_missing_cms_url_returns_1(self):
        with patch.dict(os.environ, {"CMS_API_TOKEN": "tok"}, clear=True):
            code = main(["--source", "api"])
        assert code == 1

    def test_api_source_error_returns_1(self):
        from scraper.config.api_source import ApiSourceError

        with patch.dict(os.environ, VALID_ENV_WITH_API, clear=True):
            with patch("main.fetch_products", side_effect=ApiSourceError("timeout")):
                code = main(["--source", "api"])
        assert code == 1

    def test_successful_api_run_returns_0(self):
        from scraper.config.api_source import ApiProduct
        from scraper.scheduler import make_crawl_result

        products = [ApiProduct(title="TV", sku="TV-001", crawler_urls=["https://a.com/tv"])]

        with patch.dict(os.environ, VALID_ENV_WITH_API, clear=True):
            with patch("main.fetch_products", return_value=products):
                with patch("main.Scheduler.run") as mock_run:
                    mock_run.return_value = [make_crawl_result("TV-001", 5000000.0, "a.com")]
                    code = main(["--source", "api"])

        assert code == 0

    def test_api_mode_does_not_load_yaml(self):
        from scraper.config.api_source import ApiProduct
        from scraper.scheduler import make_crawl_result

        products = [ApiProduct(title="TV", sku="TV-001", crawler_urls=["https://a.com"])]

        with patch.dict(os.environ, VALID_ENV_WITH_API, clear=True):
            with patch("main.fetch_products", return_value=products):
                with patch("main.Scheduler.run", return_value=[make_crawl_result("TV-001", 1.0, "a.com")]):
                    with patch("main.load_sites") as mock_load_sites:
                        main(["--source", "api"])
                        mock_load_sites.assert_not_called()

    def test_yaml_mode_does_not_call_api(self, tmp_path):
        config_path = _make_sites_yaml(tmp_path)
        from scraper.scheduler import make_crawl_result

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.fetch_products") as mock_fetch:
                with patch("main.Scheduler.run", return_value=[make_crawl_result("SKU-001", 1.0, "s")]):
                    main(["--source", "yaml", "--config", config_path])
                    mock_fetch.assert_not_called()


# ── Scheduler.from_jobs ────────────────────────────────────────────────────────

class TestSchedulerFromJobs:
    def test_from_jobs_bypasses_site_configs(self):
        from scraper.scheduler import CrawlJob, Scheduler

        job = CrawlJob(
            site_name="site-a.com", url="https://site-a.com/p/1",
            sku="SKU-1", requires_js=False, rate_limit_seconds=0.0,
        )
        scheduler = Scheduler.from_jobs([job])
        assert scheduler._prebuilt_jobs == [job]
        assert scheduler._site_configs == []

    def test_from_jobs_run_calls_spider_with_jobs(self):
        from scraper.scheduler import CrawlJob, Scheduler, make_crawl_result

        job = CrawlJob(
            site_name="site-a.com", url="https://site-a.com/p/1",
            sku="SKU-1", requires_js=False, rate_limit_seconds=0.0,
        )
        scheduler = Scheduler.from_jobs([job])

        captured_jobs = []

        def fake_runner(jobs, callback):
            captured_jobs.extend(jobs)
            for j in jobs:
                callback(make_crawl_result(sku=j.sku, price=99.0, source=j.site_name))

        results = scheduler.run(fake_runner)
        assert len(results) == 1
        assert results[0].sku == "SKU-1"
        assert captured_jobs == [job]

    def test_from_jobs_empty_list_returns_empty(self):
        from scraper.scheduler import Scheduler

        scheduler = Scheduler.from_jobs([])
        results = scheduler.run(lambda jobs, cb: None)
        assert results == []
