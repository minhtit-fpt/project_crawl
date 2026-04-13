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
    python main.py --encrypt
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
from scraper.scheduler import CrawlJob, CrawlOutcome, Scheduler
from scraper.spider import run_spider
from security.encryption import Encryptor
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

    # ── Step 3: load crawl targets ─────────────────────────────────────────────
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

    # ── Step 4: initialise components ─────────────────────────────────────────
    proxy_manager = ProxyManager(config.proxy_list)
    rate_limiter = RateLimiter()
    retry_handler = RetryHandler(max_retries=3, base_delay=1.0, max_delay=30.0)
    encryptor = Encryptor(config.aes_secret_key, config.aes_iv) if args.encrypt else None

    # ── Step 5: spider runner (injected into scheduler.run) ──────────────────
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

    # ── Step 6: run and print results ─────────────────────────────────────────
    logger.info("Starting crawl...")
    try:
        results = scheduler.run(spider_runner)
    except Exception as exc:
        logger.error("Crawl failed unexpectedly: %s", exc, exc_info=True)
        return 1

    print_results(results, encryptor=encryptor)

    error_count = sum(1 for r in results if r.status != "OK")
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
        "--encrypt",
        action="store_true",
        default=False,
        help="Encrypt SKU and Price columns in terminal output using AES-256",
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
        default="yaml",
        choices=["yaml", "api"],
        help="Crawl target source: 'yaml' (default) reads sites.yaml, "
             "'api' fetches from CMS API using CMS_API_URL and CMS_API_TOKEN",
    )
    return parser.parse_args(argv)


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
