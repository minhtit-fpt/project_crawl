"""
Price Crawler — entry point.

Wires together all modules and runs the crawl pipeline:

  1. Load + validate .env (fail fast on missing secrets)
  2. Parse CLI arguments
  3. Load sites.yaml
  4. Initialise ProxyManager, RateLimiter, RetryHandler
  5. Build Scrapy CrawlerProcess with PriceSpider
  6. Run crawl (blocking)
  7. Print results to terminal

Usage:
    python main.py
    python main.py --config path/to/sites.yaml
    python main.py --encrypt
    python main.py --log-level DEBUG
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Callable

from scrapy.crawler import CrawlerProcess

from scraper.config.settings import SCRAPY_SETTINGS, load_sites
from scraper.output import print_results
from scraper.proxy import ProxyManager
from scraper.rate_limiter import RateLimiter
from scraper.retry import RetryHandler
from scraper.scheduler import CrawlJob, CrawlOutcome, Scheduler
from scraper.spider import PriceSpider
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

    # ── Step 3: load sites config ──────────────────────────────────────────────
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

    # ── Step 4: initialise components ─────────────────────────────────────────
    proxy_manager = ProxyManager(config.proxy_list)
    rate_limiter = RateLimiter()
    retry_handler = RetryHandler(max_retries=3, base_delay=1.0, max_delay=30.0)
    encryptor = Encryptor(config.aes_secret_key, config.aes_iv) if args.encrypt else None

    # ── Step 5: build scheduler ────────────────────────────────────────────────
    scheduler = Scheduler(site_configs)

    # ── Step 6: spider runner (injected into scheduler.run) ───────────────────
    def spider_runner(
        jobs: list[CrawlJob],
        result_callback: Callable[[CrawlOutcome], None],
    ) -> None:
        scrapy_settings = {
            **SCRAPY_SETTINGS,
            "LOG_LEVEL": log_level,
        }
        process = CrawlerProcess(settings=scrapy_settings)
        process.crawl(
            PriceSpider,
            jobs=jobs,
            result_callback=result_callback,
            proxy_manager=proxy_manager,
            rate_limiter=rate_limiter,
            retry_handler=retry_handler,
        )
        process.start()  # blocking until all spiders finish

    # ── Step 7: run and print results ──────────────────────────────────────────
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
    return parser.parse_args(argv)


if __name__ == "__main__":
    sys.exit(main())
