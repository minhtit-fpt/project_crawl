"""
Price Crawler — entry point.

Wires together all modules and runs the crawl pipeline:

  1. Load + validate .env (fail fast on missing secrets)
  2. Parse CLI arguments
  3. Load crawl targets (from sites.yaml or CMS API)
  4. Initialise ProxyManager, RateLimiter, RetryHandler
  5. Run Scrapling-based spider (blocking)
  6. Print results to terminal

Usage:
    python main.py
    python main.py --config path/to/sites.yaml
    python main.py --source api
    python main.py --serve
    python main.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Callable

from scraper.config.api_source import ApiSourceError, build_jobs_from_api, fetch_products
from scraper.config.settings import load_sites
from scraper.output import print_results
from scraper.proxy import ProxyManager
from scraper.rate_limiter import RateLimiter
from scraper.retry import RetryHandler
from scraper.scheduler import CrawlJob, CrawlOutcome, CrawlResult, Scheduler
from scraper.spider import run_spider
from scraper.storage.database import ResultRepository
from security.env_loader import load_config


def main(argv: list[str] | None = None) -> int:
    """Run the price crawler and return exit code (0 = success, 1 = error)."""
    args = _parse_args(argv)

    # ── Step 1: load env ───────────────────────────────────────────────────────
    try:
        config = load_config()
    except EnvironmentError as exc:
        print(f"[ERROR] Configuration: {exc}", file=sys.stderr)
        return 1

    # ── Step 2: configure logging ──────────────────────────────────────────────
    log_level = args.log_level or config.log_level
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger = logging.getLogger(__name__)

    # ── Step 3: serve mode — start Pull API and exit ──────────────────────────
    if args.serve:
        return _run_api_server(config, logger)

    # ── Step 4: load crawl targets ─────────────────────────────────────────────
    if args.source == "api":
        scheduler = _build_scheduler_from_api(config, logger)
        if scheduler is None:
            return 1
    else:
        try:
            site_configs = load_sites(args.config)
        except (FileNotFoundError, ValueError) as exc:
            logger.error("Failed to load sites config: %s", exc)
            return 1

        logger.info(
            "Loaded %d site(s) with %d total SKUs",
            len(site_configs),
            sum(len(s.skus) for s in site_configs),
        )
        scheduler = Scheduler(site_configs)

    # ── Step 5: initialise components ─────────────────────────────────────────
    proxy_manager = ProxyManager(config.proxy_list)
    rate_limiter = RateLimiter()
    retry_handler = RetryHandler(max_retries=3, base_delay=1.0, max_delay=30.0)

    # ── Step 6: spider runner (injected into scheduler.run) ──────────────────
    def spider_runner(
        jobs: list[CrawlJob],
        result_callback: Callable[[CrawlOutcome], None],
    ) -> None:
        run_spider(
            jobs=jobs,
            result_callback=result_callback,
            proxy_manager=proxy_manager,
            rate_limiter=rate_limiter,
            retry_handler=retry_handler,
        )

    # ── Step 7: run and print results ─────────────────────────────────────────
    logger.info("Starting crawl...")
    repo = ResultRepository(config.sqlite_db_path)
    repo.init_schema()
    run_id = repo.start_run()

    try:
        results = scheduler.run(spider_runner)
    except Exception as exc:
        logger.error("Crawl failed unexpectedly: %s", exc, exc_info=True)
        return 1

    # ── Step 8: persist to SQLite ─────────────────────────────────────────────
    ok_count = sum(1 for r in results if isinstance(r, CrawlResult))
    error_count = len(results) - ok_count
    try:
        repo.save_results(run_id, results)
        repo.finish_run(run_id, total=len(results), ok=ok_count, errors=error_count)
        logger.info("Results saved to SQLite (run_id=%s)", run_id)
    except Exception as exc:
        logger.warning("Failed to save results to SQLite: %s", exc)

    print_results(results)

    return 0 if error_count == 0 else 2   # 2 = partial errors (not a crash)


# ── CLI argument parser ────────────────────────────────────────────────────────

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="price-crawler",
        description="Crawl product prices from configured websites.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to sites.yaml (default: scraper/config/sites.yaml)",
    )
    parser.add_argument(
        "--log-level",
        dest="log_level",
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        metavar="LEVEL",
        help="Override log level from .env (DEBUG|INFO|WARNING|ERROR)",
    )
    parser.add_argument(
        "--source",
        default="api",
        choices=["yaml", "api"],
        help="Crawl target source: 'api' (default) fetches from CMS API using "
             "CMS_API_URL and CMS_API_TOKEN, 'yaml' reads sites.yaml",
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        default=False,
        help="Start the Pull API server instead of running a crawl. "
             "CMS can query GET /prices?sku=X to retrieve stored results.",
    )
    return parser.parse_args(argv)


def _run_api_server(config: object, logger: logging.Logger) -> int:
    """Start the FastAPI Pull API server (blocking).

    Returns exit code 0 on clean shutdown, 1 on startup failure.
    """
    try:
        import uvicorn
        from scraper.api_server import create_app
    except ImportError as exc:
        logger.error(
            "Pull API requires 'fastapi' and 'uvicorn'. "
            "Run: pip install fastapi uvicorn[standard]. Error: %s", exc
        )
        return 1

    repo = ResultRepository(config.sqlite_db_path)
    repo.init_schema()

    crawl_config = {
        "cms_api_url": config.cms_api_url,
        "cms_api_token": config.cms_api_token,
        "proxy_list": config.proxy_list,
    }
    app = create_app(
        repository=repo,
        api_token=config.pull_api_token,
        crawl_config=crawl_config,
    )
    logger.info(
        "Starting Pull API on http://%s:%d",
        config.pull_api_host,
        config.pull_api_port,
    )
    logger.info("  GET  /prices?sku=SKU001     — query prices")
    logger.info("  POST /crawl/trigger         — trigger a new crawl")
    logger.info("  GET  /crawl/status/current  — check crawl progress")
    uvicorn.run(app, host=config.pull_api_host, port=config.pull_api_port)
    return 0


def _build_scheduler_from_api(config: object, logger: logging.Logger) -> Scheduler | None:
    """Fetch products from CMS API and return a Scheduler with pre-built jobs.

    Returns None (and logs the error) if credentials are missing or the API
    request fails — caller should return exit code 1.
    """
    api_url = config.cms_api_url
    api_token = config.cms_api_token

    if not api_url or not api_token:
        logger.error(
            "CMS API credentials not set. Add CMS_API_URL and CMS_API_TOKEN "
            "to your .env file before using --source api."
        )
        return None

    logger.info("Fetching crawl targets from CMS API: %s", api_url)
    try:
        products = fetch_products(api_url, api_token)
    except ApiSourceError as exc:
        logger.error("Failed to fetch from CMS API: %s", exc)
        return None

    jobs = build_jobs_from_api(products)
    logger.info("CMS API: %d products → %d crawl jobs", len(products), len(jobs))
    return Scheduler.from_jobs(jobs)


if __name__ == "__main__":
    sys.exit(main())
