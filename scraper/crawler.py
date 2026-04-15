"""
Programmatic crawl runner — used by both main.py CLI and the Pull API trigger.

Extracts the crawl pipeline into a single callable so the API endpoint can
invoke it without duplicating main.py logic.

Usage:
    from scraper.crawler import run_crawl

    run_id, results = run_crawl(
        cms_api_url=config.cms_api_url,
        cms_api_token=config.cms_api_token,
        proxy_list=config.proxy_list,
        repo=repo,
    )
"""

from __future__ import annotations

import logging

from scraper.config.api_source import ApiSourceError, build_jobs_from_api, fetch_products
from scraper.proxy import ProxyManager
from scraper.rate_limiter import RateLimiter
from scraper.retry import RetryHandler
from scraper.scheduler import CrawlJob, CrawlOutcome, CrawlResult, Scheduler
from scraper.spider import run_spider
from scraper.storage.database import ResultRepository
from typing import Callable

logger = logging.getLogger(__name__)


class CrawlError(Exception):
    """Raised when the crawl cannot start (e.g. missing credentials, API down)."""


def run_crawl(
    *,
    cms_api_url: str,
    cms_api_token: str,
    proxy_list: list[str],
    repo: ResultRepository,
) -> tuple[str, list[CrawlOutcome]]:
    """Run a full crawl cycle and persist results to database.

    Fetches jobs from CMS API, runs the spider, saves outcomes to the
    repository, and returns the run_id with all outcomes.

    Args:
        cms_api_url:   CMS endpoint to GET product/SKU list.
        cms_api_token: Auth token for the CMS API.
        proxy_list:    List of proxy URLs (may be empty).
        repo:          Initialised ResultRepository for persistence.

    Returns:
        (run_id, outcomes) — run_id is the ISO-8601 key stored in database.

    Raises:
        CrawlError: If credentials are missing or the CMS API is unreachable.
    """
    if not cms_api_url or not cms_api_token:
        raise CrawlError(
            "CMS_API_URL and CMS_API_TOKEN must be set to trigger a crawl via API."
        )

    # ── 1. Fetch jobs from CMS ─────────────────────────────────────────────────
    logger.info("Fetching crawl targets from CMS API: %s", cms_api_url)
    try:
        products = fetch_products(cms_api_url, cms_api_token)
    except ApiSourceError as exc:
        raise CrawlError(f"Failed to fetch targets from CMS API: {exc}") from exc

    jobs = build_jobs_from_api(products)
    if not jobs:
        logger.warning("CMS API returned no crawlable products — nothing to do")

    scheduler = Scheduler.from_jobs(jobs)

    # ── 2. Initialise per-run components ──────────────────────────────────────
    proxy_manager = ProxyManager(proxy_list)
    rate_limiter = RateLimiter()
    retry_handler = RetryHandler(max_retries=3, base_delay=1.0, max_delay=30.0)

    def spider_runner(
        crawl_jobs: list[CrawlJob],
        callback: Callable[[CrawlOutcome], None],
    ) -> None:
        run_spider(
            jobs=crawl_jobs,
            result_callback=callback,
            proxy_manager=proxy_manager,
            rate_limiter=rate_limiter,
            retry_handler=retry_handler,
        )

    # ── 3. Run spider ──────────────────────────────────────────────────────────
    run_id = repo.start_run()
    logger.info("Crawl run started: %s (%d jobs)", run_id, len(jobs))

    results = scheduler.run(spider_runner)

    # ── 4. Persist to database ────────────────────────────────────────────────
    ok_count = sum(1 for r in results if isinstance(r, CrawlResult))
    error_count = len(results) - ok_count

    repo.save_results(run_id, results)
    repo.finish_run(run_id, total=len(results), ok=ok_count, errors=error_count)

    logger.info(
        "Crawl run %s finished: %d OK, %d errors", run_id, ok_count, error_count
    )
    return run_id, results
