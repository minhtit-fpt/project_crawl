"""Tests for POST /crawl/trigger and GET /crawl/status/current — uses MySQL via testcontainers."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from scraper.api_server import create_app
from scraper.scheduler import make_crawl_result
from scraper.storage.database import ResultRepository

# `repo` fixture is provided by tests/conftest.py (testcontainers MySQL)
pytestmark = pytest.mark.integration

CRAWL_CONFIG = {
    "cms_api_url": "https://cms.example.com/api/products",
    "cms_api_token": "test-token",
    "proxy_list": [],
}


@pytest.fixture
def client(repo) -> TestClient:
    app = create_app(repository=repo, api_token=None, crawl_config=CRAWL_CONFIG)
    return TestClient(app)


@pytest.fixture
def no_config_client(repo) -> TestClient:
    """Client with no crawl_config — trigger should return 503."""
    app = create_app(repository=repo, api_token=None, crawl_config={})
    return TestClient(app)


# ── /crawl/status/current — idle ───────────────────────────────────────────────

def test_status_idle_before_any_crawl(client):
    resp = client.get("/crawl/status/current")
    assert resp.status_code == 200
    assert resp.json()["status"] == "idle"


# ── POST /crawl/trigger — missing config ───────────────────────────────────────

def test_trigger_returns_503_when_no_cms_config(no_config_client):
    resp = no_config_client.post("/crawl/trigger")
    assert resp.status_code == 503


# ── POST /crawl/trigger — successful ──────────────────────────────────────────

def _make_mock_run_crawl(repo: ResultRepository):
    """Return a mock run_crawl that inserts real data and returns a run_id."""
    def _mock(*, cms_api_url, cms_api_token, proxy_list, repo: ResultRepository):
        run_id = repo.start_run()
        outcomes = [make_crawl_result("SKU001", 1299000.0, "site.com")]
        repo.save_results(run_id, outcomes)
        repo.finish_run(run_id, total=1, ok=1, errors=0)
        return run_id, outcomes
    return _mock


def test_trigger_returns_202(repo, client):
    with patch("scraper.crawler.run_crawl", side_effect=_make_mock_run_crawl(repo)):
        resp = client.post("/crawl/trigger")
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "accepted"
    assert "poll_url" in body


def test_trigger_crawl_runs_and_status_becomes_done(repo, client):
    with patch("scraper.crawler.run_crawl", side_effect=_make_mock_run_crawl(repo)):
        client.post("/crawl/trigger")
        # Give background thread time to finish
        time.sleep(0.5)

    resp = client.get("/crawl/status/current")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "done"
    assert body["run_id"] != "pending"


def test_trigger_results_queryable_after_crawl(repo, client):
    with patch("scraper.crawler.run_crawl", side_effect=_make_mock_run_crawl(repo)):
        client.post("/crawl/trigger")
        time.sleep(0.5)

    resp = client.get("/prices", params={"sku": "SKU001"})
    assert resp.status_code == 200
    assert resp.json()["meta"]["total"] == 1


# ── POST /crawl/trigger — conflict ────────────────────────────────────────────

def test_trigger_409_when_already_running(repo, client):
    """Second trigger while first is still running should return 409."""
    import threading

    started = threading.Event()
    can_finish = threading.Event()

    def _slow_crawl(*, cms_api_url, cms_api_token, proxy_list, repo):
        run_id = repo.start_run()
        started.set()
        can_finish.wait(timeout=5)
        repo.finish_run(run_id, total=0, ok=0, errors=0)
        return run_id, []

    with patch("scraper.crawler.run_crawl", side_effect=_slow_crawl):
        client.post("/crawl/trigger")
        started.wait(timeout=2)  # wait until first crawl is running

        resp = client.post("/crawl/trigger")  # second trigger
        assert resp.status_code == 409

        can_finish.set()  # let first crawl finish


# ── POST /crawl/trigger — crawl error ────────────────────────────────────────

def test_status_shows_error_when_crawl_fails(client):
    from scraper.crawler import CrawlError

    def _fail(**kwargs):
        raise CrawlError("CMS API is down")

    with patch("scraper.crawler.run_crawl", side_effect=_fail):
        client.post("/crawl/trigger")
        time.sleep(0.3)

    resp = client.get("/crawl/status/current")
    body = resp.json()
    assert body["status"] == "error"
    assert "CMS API is down" in body["error"]
