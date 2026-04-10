"""Tests for scraper/output.py — terminal output formatter."""

import io
from datetime import datetime, timezone
import pytest

from scraper.output import print_results, format_results, _format_row, _fmt_timestamp
from scraper.scheduler import CrawlResult, ErrorResult
from security.encryption import Encryptor

# ── Fixtures ───────────────────────────────────────────────────────────────────

_TS = datetime(2026, 4, 10, 9, 0, 0, tzinfo=timezone.utc)
_KEY = bytes.fromhex("a" * 64)
_IV  = bytes.fromhex("b" * 32)


def _ok(sku="SKU-001", price=99.99, source="TestShop") -> CrawlResult:
    return CrawlResult(sku=sku, price=price, source=source, timestamp=_TS)


def _err(sku="SKU-002", source="TestShop", error="HTTP 404") -> ErrorResult:
    return ErrorResult(sku=sku, source=source, timestamp=_TS, error=error)


# ── print_results ──────────────────────────────────────────────────────────────

class TestPrintResults:
    def test_empty_results_prints_no_results_message(self):
        buf = io.StringIO()
        print_results([], file=buf)
        assert "No results" in buf.getvalue()

    def test_header_is_printed(self):
        buf = io.StringIO()
        print_results([_ok()], file=buf)
        output = buf.getvalue()
        assert "SKU" in output
        assert "Price" in output
        assert "Source" in output
        assert "Timestamp" in output
        assert "Status" in output

    def test_ok_result_shows_price(self):
        buf = io.StringIO()
        print_results([_ok(price=1234.56)], file=buf)
        assert "1234.56" in buf.getvalue()

    def test_ok_result_shows_sku(self):
        buf = io.StringIO()
        print_results([_ok(sku="MY-SKU")], file=buf)
        assert "MY-SKU" in buf.getvalue()

    def test_ok_result_status_is_ok(self):
        buf = io.StringIO()
        print_results([_ok()], file=buf)
        assert "OK" in buf.getvalue()

    def test_error_result_shows_error_status(self):
        buf = io.StringIO()
        print_results([_err(error="timeout")], file=buf)
        assert "Error" in buf.getvalue()
        assert "timeout" in buf.getvalue()

    def test_error_result_price_is_dash(self):
        buf = io.StringIO()
        print_results([_err()], file=buf)
        assert "—" in buf.getvalue()

    def test_summary_line_printed(self):
        buf = io.StringIO()
        print_results([_ok(), _err()], file=buf)
        output = buf.getvalue()
        assert "Total: 2" in output
        assert "OK: 1" in output
        assert "Errors: 1" in output

    def test_multiple_results_all_shown(self):
        results = [_ok(sku=f"SKU-{i}") for i in range(5)]
        buf = io.StringIO()
        print_results(results, file=buf)
        output = buf.getvalue()
        for i in range(5):
            assert f"SKU-{i}" in output

    def test_separator_line_present(self):
        buf = io.StringIO()
        print_results([_ok()], file=buf)
        # Separator is a line of dashes
        assert "---" in buf.getvalue()


# ── format_results ─────────────────────────────────────────────────────────────

class TestFormatResults:
    def test_returns_string(self):
        result = format_results([_ok()])
        assert isinstance(result, str)

    def test_contains_sku(self):
        result = format_results([_ok(sku="MYSKU")])
        assert "MYSKU" in result


# ── Encryption integration ─────────────────────────────────────────────────────

class TestPrintResultsWithEncryption:
    def test_sku_is_encrypted_not_plain(self):
        enc = Encryptor(_KEY, _IV)
        buf = io.StringIO()
        print_results([_ok(sku="SKU-SECRET")], encryptor=enc, file=buf)
        output = buf.getvalue()
        # Plain SKU should NOT appear
        assert "SKU-SECRET" not in output

    def test_price_is_encrypted_not_plain(self):
        enc = Encryptor(_KEY, _IV)
        buf = io.StringIO()
        print_results([_ok(price=9999.99)], encryptor=enc, file=buf)
        output = buf.getvalue()
        assert "9999.99" not in output

    def test_error_price_dash_not_encrypted(self):
        enc = Encryptor(_KEY, _IV)
        buf = io.StringIO()
        print_results([_err()], encryptor=enc, file=buf)
        # Error rows still show "—" (no price to encrypt)
        assert "—" in buf.getvalue()


# ── _format_row ────────────────────────────────────────────────────────────────

class TestFormatRow:
    def test_ok_row_has_five_columns(self):
        row = _format_row(_ok(), encryptor=None)
        assert len(row) == 5

    def test_ok_row_status_is_ok(self):
        row = _format_row(_ok(), encryptor=None)
        assert row[4] == "OK"

    def test_ok_row_price_formatted_to_2dp(self):
        row = _format_row(_ok(price=5.0), encryptor=None)
        assert row[1] == "5.00"

    def test_error_row_status_contains_error(self):
        row = _format_row(_err(error="connection refused"), encryptor=None)
        assert "Error" in row[4]
        assert "connection refused" in row[4]

    def test_error_row_price_is_dash(self):
        row = _format_row(_err(), encryptor=None)
        assert row[1] == "—"


# ── _fmt_timestamp ─────────────────────────────────────────────────────────────

class TestFmtTimestamp:
    def test_formats_correctly(self):
        ts = datetime(2026, 4, 10, 12, 30, 45, tzinfo=timezone.utc)
        assert _fmt_timestamp(ts) == "2026-04-10 12:30:45"
