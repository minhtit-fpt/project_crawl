"""Tests for scraper/scheduler.py — Scheduler, CrawlJob, CrawlResult, ErrorResult."""

from datetime import timezone
import pytest

from scraper.config.settings import SiteConfig, SelectorConfig
from scraper.scheduler import (
    Scheduler,
    CrawlJob,
    CrawlResult,
    ErrorResult,
    make_crawl_result,
    make_error_result,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _selector() -> SelectorConfig:
    return SelectorConfig(price_css=".price", price_xpath="//span[@class='price']")


def _site(name: str = "Shop", skus: list[str] | None = None, requires_js: bool = False) -> SiteConfig:
    return SiteConfig(
        name=name,
        base_url=f"https://{name.lower()}.com/product/{{sku}}",
        skus=skus or ["SKU-001", "SKU-002"],
        selectors=_selector(),
        requires_js=requires_js,
        rate_limit_seconds=1.0,
    )


# ── Scheduler.build_jobs ───────────────────────────────────────────────────────

class TestBuildJobs:
    def test_expands_single_site_two_skus(self):
        scheduler = Scheduler([_site(skus=["A", "B"])])
        jobs = scheduler.build_jobs()
        assert len(jobs) == 2

    def test_job_url_has_sku_substituted(self):
        scheduler = Scheduler([_site(name="Store", skus=["X99"])])
        job = scheduler.build_jobs()[0]
        assert "X99" in job.url
        assert "{sku}" not in job.url

    def test_job_fields_correct(self):
        site = _site(name="MyShop", skus=["SKU-1"], requires_js=True)
        scheduler = Scheduler([site])
        job = scheduler.build_jobs()[0]
        assert job.site_name == "MyShop"
        assert job.sku == "SKU-1"
        assert job.requires_js is True
        assert job.rate_limit_seconds == 1.0

    def test_multiple_sites_multiply_jobs(self):
        sites = [
            _site(name="A", skus=["1", "2", "3"]),
            _site(name="B", skus=["4", "5"]),
        ]
        jobs = Scheduler(sites).build_jobs()
        assert len(jobs) == 5

    def test_all_jobs_are_crawl_job_instances(self):
        jobs = Scheduler([_site()]).build_jobs()
        assert all(isinstance(j, CrawlJob) for j in jobs)

    def test_empty_site_list_raises(self):
        with pytest.raises(ValueError):
            Scheduler([])


# ── Scheduler.collect_result / get_results ────────────────────────────────────

class TestCollectResults:
    def test_collect_crawl_result(self):
        scheduler = Scheduler([_site()])
        result = make_crawl_result(sku="SKU-001", price=99.9, source="Shop")
        scheduler.collect_result(result)
        assert result in scheduler.get_results()

    def test_collect_error_result(self):
        scheduler = Scheduler([_site()])
        err = make_error_result(sku="SKU-001", source="Shop", error="HTTP 404")
        scheduler.collect_result(err)
        assert err in scheduler.get_results()

    def test_get_results_returns_copy(self):
        scheduler = Scheduler([_site()])
        results = scheduler.get_results()
        results.append(make_error_result("X", "Y", "Z"))
        # Original should be unaffected
        assert len(scheduler.get_results()) == 0

    def test_collect_multiple_results(self):
        scheduler = Scheduler([_site(skus=["A", "B", "C"])])
        for sku in ["A", "B", "C"]:
            scheduler.collect_result(make_crawl_result(sku=sku, price=1.0, source="S"))
        assert len(scheduler.get_results()) == 3


# ── Scheduler.run ─────────────────────────────────────────────────────────────

class TestRun:
    def test_run_calls_spider_runner_with_jobs(self):
        received_jobs = []

        def fake_runner(jobs, callback):
            received_jobs.extend(jobs)
            for j in jobs:
                callback(make_crawl_result(sku=j.sku, price=1.0, source=j.site_name))

        scheduler = Scheduler([_site(skus=["A", "B"])])
        results = scheduler.run(fake_runner)
        assert len(received_jobs) == 2
        assert len(results) == 2

    def test_run_clears_previous_results(self):
        def fake_runner(jobs, callback):
            for j in jobs:
                callback(make_crawl_result(sku=j.sku, price=5.0, source=j.site_name))

        scheduler = Scheduler([_site(skus=["X"])])
        scheduler.run(fake_runner)
        results = scheduler.run(fake_runner)
        # Should not accumulate across runs
        assert len(results) == 1

    def test_run_handles_mixed_results(self):
        def fake_runner(jobs, callback):
            callback(make_crawl_result(sku="A", price=10.0, source="S"))
            callback(make_error_result(sku="B", source="S", error="timeout"))

        scheduler = Scheduler([_site(skus=["A", "B"])])
        results = scheduler.run(fake_runner)
        ok = [r for r in results if isinstance(r, CrawlResult)]
        errors = [r for r in results if isinstance(r, ErrorResult)]
        assert len(ok) == 1
        assert len(errors) == 1


# ── Factory helpers ────────────────────────────────────────────────────────────

class TestFactories:
    def test_make_crawl_result_has_utc_timestamp(self):
        r = make_crawl_result(sku="S", price=1.0, source="X")
        assert r.timestamp.tzinfo == timezone.utc
        assert r.status == "OK"

    def test_make_error_result_has_utc_timestamp(self):
        e = make_error_result(sku="S", source="X", error="boom")
        assert e.timestamp.tzinfo == timezone.utc
        assert e.status == "Error"
        assert e.error == "boom"
