"""Storage data models (separate from scheduler dataclasses)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CrawlRun:
    """Metadata for one complete crawl execution."""

    id: str                          # ISO-8601 timestamp used as unique key
    started_at: datetime
    finished_at: datetime | None
    total_jobs: int
    ok_count: int
    error_count: int
