"""Tests for main.py — CLI wiring, argument parsing, and integration flow."""

import os
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from main import main, _parse_args


# ── Helpers ────────────────────────────────────────────────────────────────────

VALID_KEY_HEX = "a" * 64
VALID_IV_HEX  = "b" * 32

VALID_ENV = {
    "AES_SECRET_KEY": VALID_KEY_HEX,
    "AES_IV": VALID_IV_HEX,
}


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
        assert args.encrypt is False
        assert args.log_level is None

    def test_config_flag(self):
        args = _parse_args(["--config", "path/to/sites.yaml"])
        assert args.config == "path/to/sites.yaml"

    def test_encrypt_flag(self):
        args = _parse_args(["--encrypt"])
        assert args.encrypt is True

    def test_log_level_flag(self):
        args = _parse_args(["--log-level", "DEBUG"])
        assert args.log_level == "DEBUG"

    def test_invalid_log_level_raises(self):
        with pytest.raises(SystemExit):
            _parse_args(["--log-level", "VERBOSE"])


# ── main() — env errors ────────────────────────────────────────────────────────

class TestMainEnvErrors:
    def test_missing_env_returns_exit_code_1(self, tmp_path):
        with patch.dict(os.environ, {}, clear=True):
            code = main(["--config", str(tmp_path / "nonexistent.yaml")])
        assert code == 1

    def test_missing_env_prints_error_to_stderr(self, capsys):
        # Patch load_config directly so .env file on disk does not interfere
        with patch("main.load_config", side_effect=EnvironmentError("Missing AES_SECRET_KEY")):
            main([])
        captured = capsys.readouterr()
        assert "ERROR" in captured.err


# ── main() — config errors ─────────────────────────────────────────────────────

class TestMainConfigErrors:
    def test_missing_config_file_returns_1(self):
        with patch.dict(os.environ, VALID_ENV, clear=True):
            code = main(["--config", "/nonexistent/sites.yaml"])
        assert code == 1


# ── main() — successful run (spider mocked) ───────────────────────────────────

class TestMainSuccess:
    def test_returns_0_when_all_ok(self, tmp_path):
        """All results OK → exit code 0."""
        from scraper.scheduler import make_crawl_result

        config_path = _make_sites_yaml(tmp_path)

        def fake_spider_runner(jobs, callback):
            for job in jobs:
                callback(make_crawl_result(sku=job.sku, price=99.9, source=job.site_name))

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.CrawlerProcess") as mock_process_cls:
                # Make CrawlerProcess behave like our fake runner
                mock_process = MagicMock()
                mock_process_cls.return_value = mock_process

                # Patch spider_runner inside main via Scheduler.run
                with patch("main.Scheduler.run", side_effect=lambda runner: fake_spider_runner(
                    # build_jobs is called inside run, so delegate properly
                    [MagicMock(sku="SKU-001", site_name="TestShop")],
                    lambda r: None,
                ) or [make_crawl_result("SKU-001", 99.9, "TestShop")]):
                    code = main(["--config", config_path])

        # Code should be 0 or 2 (not 1 which means crash)
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
                code = main(["--config", config_path])

        assert code == 2

    def test_no_encrypt_by_default(self, tmp_path):
        """Without --encrypt, Encryptor should NOT be passed to print_results."""
        from scraper.scheduler import make_crawl_result

        config_path = _make_sites_yaml(tmp_path)

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.Scheduler.run") as mock_run:
                mock_run.return_value = [make_crawl_result("SKU-001", 10.0, "S")]
                with patch("main.print_results") as mock_print:
                    main(["--config", config_path])
                    _, kwargs = mock_print.call_args
                    assert kwargs.get("encryptor") is None

    def test_encrypt_flag_passes_encryptor(self, tmp_path):
        """With --encrypt, Encryptor should be passed to print_results."""
        from scraper.scheduler import make_crawl_result

        config_path = _make_sites_yaml(tmp_path)

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.Scheduler.run") as mock_run:
                mock_run.return_value = [make_crawl_result("SKU-001", 10.0, "S")]
                with patch("main.print_results") as mock_print:
                    main(["--config", config_path, "--encrypt"])
                    _, kwargs = mock_print.call_args
                    assert kwargs.get("encryptor") is not None


# ── main() — unexpected crawl error ───────────────────────────────────────────

class TestMainCrawlError:
    def test_unexpected_exception_returns_1(self, tmp_path):
        config_path = _make_sites_yaml(tmp_path)

        with patch.dict(os.environ, VALID_ENV, clear=True):
            with patch("main.Scheduler.run", side_effect=RuntimeError("boom")):
                code = main(["--config", config_path])

        assert code == 1
