"""Tests for scraper.api_server (FastAPI Pull API) — uses MySQL via testcontainers."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from scraper.api_server import create_app
from scraper.scheduler import make_crawl_result, make_error_result
from scraper.storage.database import ResultRepository

# `repo` fixture is provided by tests/conftest.py (testcontainers MySQL)
pytestmark = pytest.mark.integration


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def client(repo) -> TestClient:
    """Unauthenticated client (no PULL_API_TOKEN)."""
    app = create_app(repository=repo, api_token=None)
    return TestClient(app)


@pytest.fixture
def auth_client(repo) -> TestClient:
    """Client with PULL_API_TOKEN=secret."""
    app = create_app(repository=repo, api_token="secret")
    return TestClient(app)


# ── /health ────────────────────────────────────────────────────────────────────

def test_health_returns_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ── /prices — unauthenticated ──────────────────────────────────────────────────

def test_prices_requires_sku_param(client):
    resp = client.get("/prices")
    assert resp.status_code == 422  # FastAPI validation error


def test_prices_unknown_sku_returns_empty_data(client):
    resp = client.get("/prices", params={"sku": "GHOST"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["sku"] == "GHOST"
    assert body["data"] == []
    assert body["meta"]["total"] == 0


def test_prices_returns_crawl_result(repo, client):
    run_id = repo.start_run()
    repo.save_results(run_id, [make_crawl_result("SKU001", 1299000.0, "dienmayxanh.com")])

    resp = client.get("/prices", params={"sku": "SKU001"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["sku"] == "SKU001"
    assert len(body["data"]) == 1
    row = body["data"][0]
    assert row["price"] == pytest.approx(1299000.0)
    assert row["source"] == "dienmayxanh.com"
    assert row["status"] == "OK"
    assert row["error"] is None
    assert "crawled_at" in row
    assert "run_id" in row


def test_prices_returns_error_result(repo, client):
    run_id = repo.start_run()
    repo.save_results(run_id, [make_error_result("SKU002", "tiki.vn", "404 Not Found")])

    resp = client.get("/prices", params={"sku": "SKU002"})
    assert resp.status_code == 200
    row = resp.json()["data"][0]
    assert row["status"] == "Error"
    assert row["price"] is None
    assert row["error"] == "404 Not Found"


def test_prices_limit_param(repo, client):
    run_id = repo.start_run()
    outcomes = [make_crawl_result("SKU003", float(i), "site.com") for i in range(10)]
    repo.save_results(run_id, outcomes)

    resp = client.get("/prices", params={"sku": "SKU003", "limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["data"]) == 3
    assert body["meta"]["limit"] == 3


def test_prices_limit_max_enforced(client):
    resp = client.get("/prices", params={"sku": "X", "limit": 9999})
    assert resp.status_code == 422  # exceeds le=200


def test_meta_total_reflects_actual_count(repo, client):
    run_id = repo.start_run()
    repo.save_results(run_id, [
        make_crawl_result("SKU004", 1.0, "a.com"),
        make_crawl_result("SKU004", 2.0, "b.com"),
    ])
    resp = client.get("/prices", params={"sku": "SKU004"})
    assert resp.json()["meta"]["total"] == 2


# ── /prices — authenticated ────────────────────────────────────────────────────

def test_auth_missing_header_returns_401(auth_client):
    resp = auth_client.get("/prices", params={"sku": "X"})
    assert resp.status_code == 401


def test_auth_wrong_token_returns_403(auth_client):
    resp = auth_client.get(
        "/prices", params={"sku": "X"},
        headers={"Authorization": "Bearer wrong"}
    )
    assert resp.status_code == 403


def test_auth_correct_token_allows_access(repo, auth_client):
    run_id = repo.start_run()
    repo.save_results(run_id, [make_crawl_result("SKU005", 500.0, "lazada.vn")])

    resp = auth_client.get(
        "/prices", params={"sku": "SKU005"},
        headers={"Authorization": "Bearer secret"}
    )
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 1


def test_health_skips_auth(auth_client):
    """/health should be accessible without a token."""
    resp = auth_client.get("/health")
    assert resp.status_code == 200
