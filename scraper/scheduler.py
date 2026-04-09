"""
Scheduler — orchestrates crawl jobs.

Responsibilities:
  1. Expand SiteConfig list into a flat list of CrawlJob objects (one per site×SKU pair).
  2. Hand jobs to the Scrapy spider via CrawlerProcess.
  3. Collect CrawlResult / ErrorResult objects produced by the spider.
  4. Pass collected results to the output formatter.

The scheduler is intentionally stateless between runs: each call to run()
produces a fresh job list and fresh results.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from scraper.config.settings import SiteConfig, SelectorConfig

logger = logging.getLogger(__name__)


# ── Data models ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CrawlJob:
    """A single unit of work: fetch price for one SKU on one site."""
    site_name: str
    url: str                     # base_url with {sku} already substituted
    sku: str
    selectors: SelectorConfig
    requires_js: bool
    rate_limit_seconds: float


@dataclass
class CrawlResult:
    """Successful price extraction."""
    sku: str
    price: float
    source: str                  # site name
    timestamp: datetime
    status: str = "OK"


@dataclass
class ErrorResult:
    """Failed crawl — price could not be extracted."""
    sku: str
    source: str
    timestamp: datetime
    error: str
    status: str = "Error"


# Union type for results collected by the scheduler
CrawlOutcome = CrawlResult | ErrorResult


# ── Scheduler ─────────────────────────────────────────────────────────────────

class Scheduler:
    """Expands site configs into jobs and orchestrates their execution.

    Usage:
        scheduler = Scheduler(site_configs)
        results = scheduler.run()   # blocking
    """

    def __init__(self, site_configs: list[SiteConfig]) -> None:
        if not site_configs:
            raise ValueError("site_configs must not be empty")
        self._site_configs = site_configs
        self._results: list[CrawlOutcome] = []

    # ── Public API ─────────────────────────────────────────────────────────────

    def build_jobs(self) -> list[CrawlJob]:
        """Expand all site × SKU combinations into a flat CrawlJob list."""
        jobs: list[CrawlJob] = []
        for site in self._site_configs:
            for sku in site.skus:
                url = site.base_url.replace("{sku}", sku)
                jobs.append(
                    CrawlJob(
                        site_name=site.name,
                        url=url,
                        sku=sku,
                        selectors=site.selectors,
                        requires_js=site.requires_js,
                        rate_limit_seconds=site.rate_limit_seconds,
                    )
                )
        logger.info("Built %d crawl jobs from %d sites", len(jobs), len(self._site_configs))
        return jobs

    def collect_result(self, outcome: CrawlOutcome) -> None:
        """Called by the spider to register a completed job outcome."""
        self._results.append(outcome)

    def get_results(self) -> list[CrawlOutcome]:
        """Return a snapshot of all collected results (immutable copy)."""
        return list(self._results)

    def run(self, spider_runner: "SpiderRunner") -> list[CrawlOutcome]:
        """Build jobs, run the spider, and return all outcomes.

        Args:
            spider_runner: Any callable that accepts a list[CrawlJob] and a
                           result collector callback, then blocks until done.
                           (Injected to keep the scheduler testable without
                           a real Scrapy process.)

        Returns:
            List of CrawlResult and ErrorResult objects.
        """
        self._results.clear()
        jobs = self.build_jobs()

        if not jobs:
            logger.warning("No crawl jobs generated — check sites.yaml SKU lists")
            return []

        spider_runner(jobs, self.collect_result)

        total = len(self._results)
        ok = sum(1 for r in self._results if isinstance(r, CrawlResult))
        errors = total - ok
        logger.info("Crawl complete: %d OK, %d errors (total %d)", ok, errors, total)

        return self.get_results()


# ── Convenience factory ────────────────────────────────────────────────────────

def make_error_result(sku: str, source: str, error: str) -> ErrorResult:
    """Create an ErrorResult with the current UTC timestamp."""
    return ErrorResult(
        sku=sku,
        source=source,
        timestamp=datetime.now(tz=timezone.utc),
        error=error,
    )


def make_crawl_result(sku: str, price: float, source: str) -> CrawlResult:
    """Create a CrawlResult with the current UTC timestamp."""
    return CrawlResult(
        sku=sku,
        price=price,
        source=source,
        timestamp=datetime.now(tz=timezone.utc),
    )
