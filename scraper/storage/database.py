"""
SQLite repository for persisting crawl results.

Schema:
  crawl_runs    — one row per crawler execution (with timing metadata)
  crawl_results — one row per (sku, source) result from a run

WAL mode is enabled so the Pull API can read concurrently while the
crawler writes without blocking.

Usage:
    repo = ResultRepository("data/crawl_results.db")
    repo.init_schema()

    run_id = repo.start_run()
    repo.save_results(run_id, outcomes)
    repo.finish_run(run_id, total=5, ok=4, errors=1)

    prices = repo.get_prices_by_sku("SKU001", limit=20)
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator

from scraper.scheduler import CrawlOutcome, CrawlResult

logger = logging.getLogger(__name__)

# ── SQL statements ─────────────────────────────────────────────────────────────

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS crawl_runs (
    id          TEXT PRIMARY KEY,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    total_jobs  INTEGER DEFAULT 0,
    ok_count    INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0
);
"""

_CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS crawl_results (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    crawl_run_id TEXT NOT NULL REFERENCES crawl_runs(id),
    sku          TEXT NOT NULL,
    price        REAL,
    source       TEXT NOT NULL,
    crawled_at   TEXT NOT NULL,
    status       TEXT NOT NULL,
    error        TEXT
);
"""

_CREATE_IDX_SKU = "CREATE INDEX IF NOT EXISTS idx_results_sku ON crawl_results(sku);"
_CREATE_IDX_RUN = "CREATE INDEX IF NOT EXISTS idx_results_run ON crawl_results(crawl_run_id);"

_INSERT_RUN = """
INSERT INTO crawl_runs (id, started_at, finished_at, total_jobs, ok_count, error_count)
VALUES (?, ?, NULL, 0, 0, 0);
"""

_FINISH_RUN = """
UPDATE crawl_runs
SET finished_at = ?, total_jobs = ?, ok_count = ?, error_count = ?
WHERE id = ?;
"""

_INSERT_RESULT = """
INSERT INTO crawl_results (crawl_run_id, sku, price, source, crawled_at, status, error)
VALUES (?, ?, ?, ?, ?, ?, ?);
"""

_SELECT_BY_SKU = """
SELECT sku, price, source, crawled_at, status, error, crawl_run_id
FROM   crawl_results
WHERE  sku = ?
ORDER  BY crawled_at DESC, id DESC
LIMIT  ?;
"""


# ── Repository ─────────────────────────────────────────────────────────────────

class ResultRepository:
    """Thin SQLite wrapper — one instance per process, thread-safe via WAL."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    # ── Public API ─────────────────────────────────────────────────────────────

    def init_schema(self) -> None:
        """Create tables and indexes if they do not yet exist."""
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute(_CREATE_RUNS)
            conn.execute(_CREATE_RESULTS)
            conn.execute(_CREATE_IDX_SKU)
            conn.execute(_CREATE_IDX_RUN)
        logger.debug("SQLite schema initialised at %s", self._db_path)

    def start_run(self) -> str:
        """Insert a new crawl_run row and return its ID (ISO-8601 timestamp)."""
        run_id = _now_iso()
        with self._connect() as conn:
            conn.execute(_INSERT_RUN, (run_id, run_id))
        logger.debug("Started crawl run %s", run_id)
        return run_id

    def finish_run(
        self,
        run_id: str,
        *,
        total: int,
        ok: int,
        errors: int,
    ) -> None:
        """Update the crawl_run row with final counts and finish time."""
        finished_at = _now_iso()
        with self._connect() as conn:
            conn.execute(_FINISH_RUN, (finished_at, total, ok, errors, run_id))
        logger.debug(
            "Finished crawl run %s: %d OK, %d errors", run_id, ok, errors
        )

    def save_results(
        self,
        run_id: str,
        outcomes: list[CrawlOutcome],
    ) -> None:
        """Persist all outcomes from one crawl run in a single transaction."""
        rows = [_outcome_to_row(run_id, o) for o in outcomes]
        with self._connect() as conn:
            conn.executemany(_INSERT_RESULT, rows)
        logger.debug("Saved %d results for run %s", len(rows), run_id)

    def get_prices_by_sku(
        self,
        sku: str,
        limit: int = 50,
    ) -> list[dict]:
        """Return the most recent crawled prices for a given SKU.

        Args:
            sku:   Product SKU to look up.
            limit: Maximum number of rows to return (newest first).

        Returns:
            List of dicts with keys:
                sku, price, source, crawled_at, status, error, run_id
        """
        with self._connect() as conn:
            cursor = conn.execute(_SELECT_BY_SKU, (sku, limit))
            rows = cursor.fetchall()

        return [
            {
                "sku": row[0],
                "price": row[1],
                "source": row[2],
                "crawled_at": row[3],
                "status": row[4],
                "error": row[5],
                "run_id": row[6],
            }
            for row in rows
        ]

    # ── Private ────────────────────────────────────────────────────────────────

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Open a connection, yield it inside a transaction, then close."""
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            with conn:          # auto-commit on exit, rollback on exception
                yield conn
        finally:
            conn.close()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _outcome_to_row(run_id: str, outcome: CrawlOutcome) -> tuple:
    """Convert a CrawlResult or ErrorResult into a DB insert tuple."""
    if isinstance(outcome, CrawlResult):
        return (
            run_id,
            outcome.sku,
            outcome.price,
            outcome.source,
            outcome.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
            "OK",
            None,
        )
    # ErrorResult
    return (
        run_id,
        outcome.sku,
        None,
        outcome.source,
        outcome.timestamp.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "Error",
        outcome.error,
    )
