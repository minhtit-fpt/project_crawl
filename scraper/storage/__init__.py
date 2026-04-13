"""Storage layer — SQLite persistence for crawl results."""

from scraper.storage.database import ResultRepository
from scraper.storage.models import CrawlRun

__all__ = ["ResultRepository", "CrawlRun"]
