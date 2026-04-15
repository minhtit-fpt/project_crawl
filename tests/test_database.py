"""Tests for scraper.storage.database (ResultRepository) — uses MySQL via testcontainers."""

from __future__ import annotations

import pytest

from scraper.scheduler import make_crawl_result, make_error_result
from scraper.storage.database import ResultRepository

# `repo` fixture is provided by tests/conftest.py (testcontainers MySQL)
pytestmark = pytest.mark.integration


# ── init_schema ────────────────────────────────────────────────────────────────

def test_init_schema_creates_tables(repo):
    """Schema can be initialised multiple times without error (idempotent)."""
    repo.init_schema()  # second call — should not raise


# ── start_run / finish_run ─────────────────────────────────────────────────────

def test_start_run_returns_string(repo):
    run_id = repo.start_run()
    assert isinstance(run_id, str)
    assert "T" in run_id  # ISO-8601 shape


def test_finish_run_does_not_raise(repo):
    run_id = repo.start_run()
    repo.finish_run(run_id, total=5, ok=4, errors=1)  # should not raise


# ── save_results ───────────────────────────────────────────────────────────────

def test_save_crawl_result(repo):
    run_id = repo.start_run()
    result = make_crawl_result(sku="SKU001", price=1299000.0, source="site-a.com")
    repo.save_results(run_id, [result])

    rows = repo.get_prices_by_sku("SKU001")
    assert len(rows) == 1
    assert rows[0]["sku"] == "SKU001"
    assert rows[0]["price"] == pytest.approx(1299000.0)
    assert rows[0]["source"] == "site-a.com"
    assert rows[0]["status"] == "OK"
    assert rows[0]["error"] is None
    assert rows[0]["run_id"] == run_id


def test_save_error_result(repo):
    run_id = repo.start_run()
    err = make_error_result(sku="SKU002", source="site-b.com", error="Timeout")
    repo.save_results(run_id, [err])

    rows = repo.get_prices_by_sku("SKU002")
    assert len(rows) == 1
    assert rows[0]["status"] == "Error"
    assert rows[0]["price"] is None
    assert rows[0]["error"] == "Timeout"


def test_save_mixed_results(repo):
    run_id = repo.start_run()
    outcomes = [
        make_crawl_result("SKU001", 999.0, "site-a.com"),
        make_error_result("SKU001", "site-b.com", "404"),
    ]
    repo.save_results(run_id, outcomes)

    rows = repo.get_prices_by_sku("SKU001")
    assert len(rows) == 2


def test_save_empty_results(repo):
    run_id = repo.start_run()
    repo.save_results(run_id, [])  # should not raise
    rows = repo.get_prices_by_sku("SKU001")
    assert rows == []


# ── get_prices_by_sku ──────────────────────────────────────────────────────────

def test_unknown_sku_returns_empty(repo):
    rows = repo.get_prices_by_sku("UNKNOWN_SKU")
    assert rows == []


def test_limit_is_respected(repo):
    run_id = repo.start_run()
    # Insert 5 results for the same SKU
    outcomes = [
        make_crawl_result("SKU001", float(i * 100), "site.com")
        for i in range(5)
    ]
    repo.save_results(run_id, outcomes)

    rows = repo.get_prices_by_sku("SKU001", limit=3)
    assert len(rows) == 3


def test_results_ordered_newest_first(repo):
    """Rows should come back newest inserted first (ORDER BY crawled_at DESC, id DESC)."""
    run1 = repo.start_run()
    repo.save_results(run1, [make_crawl_result("SKU001", 100.0, "site.com")])
    run2 = repo.start_run()
    repo.save_results(run2, [make_crawl_result("SKU001", 200.0, "site.com")])

    rows = repo.get_prices_by_sku("SKU001", limit=10)
    # Higher AUTO_INCREMENT id (inserted last) comes first when timestamps tie
    assert rows[0]["price"] == pytest.approx(200.0)
    assert rows[1]["price"] == pytest.approx(100.0)


def test_sku_isolation(repo):
    """Results for SKU-A should not appear when querying SKU-B."""
    run_id = repo.start_run()
    repo.save_results(run_id, [make_crawl_result("SKU-A", 1.0, "s.com")])
    assert repo.get_prices_by_sku("SKU-B") == []
