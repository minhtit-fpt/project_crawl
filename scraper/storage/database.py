"""
MySQL repository for persisting crawl results.

Schema:
  crawl_runs    — one row per crawler execution (with timing metadata)
  crawl_results — one row per (sku, source) result from a run

A connection pool is used so the Pull API can serve reads concurrently
while the crawler writes without blocking.

Usage:
    repo = ResultRepository("mysql://crawler:password@localhost:3306/price_crawler")
    repo.init_schema()

    run_id = repo.start_run()
    repo.save_results(run_id, outcomes)
    repo.finish_run(run_id, total=5, ok=4, errors=1)

    prices = repo.get_prices_by_sku("SKU001", limit=20)
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from typing import Generator
from urllib.parse import urlparse, unquote

import mysql.connector
from mysql.connector import pooling

from scraper.scheduler import CrawlOutcome, CrawlResult

logger = logging.getLogger(__name__)

# ── SQL statements ─────────────────────────────────────────────────────────────

_CREATE_RUNS = """
CREATE TABLE IF NOT EXISTS crawl_runs (
    id          VARCHAR(64) PRIMARY KEY,
    started_at  VARCHAR(64) NOT NULL,
    finished_at VARCHAR(64),
    total_jobs  INT DEFAULT 0,
    ok_count    INT DEFAULT 0,
    error_count INT DEFAULT 0
);
"""

_CREATE_RESULTS = """
CREATE TABLE IF NOT EXISTS crawl_results (
    id           BIGINT PRIMARY KEY AUTO_INCREMENT,
    crawl_run_id VARCHAR(64) NOT NULL,
    sku          VARCHAR(128) NOT NULL,
    price        DOUBLE,
    source       VARCHAR(256) NOT NULL,
    crawled_at   VARCHAR(64) NOT NULL,
    status       VARCHAR(16) NOT NULL,
    error        TEXT,
    FOREIGN KEY (crawl_run_id) REFERENCES crawl_runs(id)
);
"""

_CREATE_IDX_SKU = (
    "CREATE INDEX idx_results_sku ON crawl_results(sku);"
)
_CREATE_IDX_RUN = (
    "CREATE INDEX idx_results_run ON crawl_results(crawl_run_id);"
)

_INSERT_RUN = """
INSERT INTO crawl_runs (id, started_at, finished_at, total_jobs, ok_count, error_count)
VALUES (%s, %s, NULL, 0, 0, 0);
"""

_FINISH_RUN = """
UPDATE crawl_runs
SET finished_at = %s, total_jobs = %s, ok_count = %s, error_count = %s
WHERE id = %s;
"""

_INSERT_RESULT = """
INSERT INTO crawl_results (crawl_run_id, sku, price, source, crawled_at, status, error)
VALUES (%s, %s, %s, %s, %s, %s, %s);
"""

_SELECT_BY_SKU = """
SELECT sku, price, source, crawled_at, status, error, crawl_run_id
FROM   crawl_results
WHERE  sku = %s
ORDER  BY crawled_at DESC, id DESC
LIMIT  %s;
"""


# ── Repository ─────────────────────────────────────────────────────────────────

class ResultRepository:
    """MySQL-backed repository — one instance per process, uses connection pool."""

    def __init__(self, database_url: str, pool_size: int = 5) -> None:
        config = _parse_database_url(database_url)
        self._pool = pooling.MySQLConnectionPool(
            pool_name="crawl_pool",
            pool_size=pool_size,
            pool_reset_session=True,
            **config,
        )
        logger.debug("MySQL connection pool created (size=%d)", pool_size)

    # ── Public API ─────────────────────────────────────────────────────────────

    def init_schema(self) -> None:
        """Create tables and indexes if they do not yet exist."""
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(_CREATE_RUNS)
            cursor.execute(_CREATE_RESULTS)
            # MySQL 8.0 does not support IF NOT EXISTS for CREATE INDEX
            # so we handle the duplicate-key error gracefully.
            _create_index_if_missing(cursor, _CREATE_IDX_SKU)
            _create_index_if_missing(cursor, _CREATE_IDX_RUN)
            cursor.close()
        logger.debug("MySQL schema initialised")

    def start_run(self) -> str:
        """Insert a new crawl_run row and return its ID (ISO-8601 timestamp)."""
        run_id = _now_iso()
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.execute(_INSERT_RUN, (run_id, run_id))
            cursor.close()
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
            cursor = conn.cursor()
            cursor.execute(_FINISH_RUN, (finished_at, total, ok, errors, run_id))
            cursor.close()
        logger.debug(
            "Finished crawl run %s: %d OK, %d errors", run_id, ok, errors
        )

    def save_results(
        self,
        run_id: str,
        outcomes: list[CrawlOutcome],
    ) -> None:
        """Persist all outcomes from one crawl run in a single transaction."""
        if not outcomes:
            return
        rows = [_outcome_to_row(run_id, o) for o in outcomes]
        with self._connect() as conn:
            cursor = conn.cursor()
            cursor.executemany(_INSERT_RESULT, rows)
            cursor.close()
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
            cursor = conn.cursor()
            cursor.execute(_SELECT_BY_SKU, (sku, limit))
            rows = cursor.fetchall()
            cursor.close()

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
    def _connect(self) -> Generator[mysql.connector.MySQLConnection, None, None]:
        """Get a connection from the pool, commit on success, rollback on error."""
        conn = self._pool.get_connection()
        try:
            conn.autocommit = False
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()  # returns connection to pool


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_database_url(url: str) -> dict:
    """Parse a MySQL URL into keyword arguments for mysql.connector.

    Supports:
        mysql://user:password@host:port/database
        mysql+mysqlconnector://user:password@host:port/database

    Returns:
        Dict with keys: host, port, user, password, database
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 3306
    user = unquote(parsed.username or "")
    password = unquote(parsed.password or "")
    database = parsed.path.lstrip("/")

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "database": database,
    }


def _create_index_if_missing(cursor: mysql.connector.cursor.MySQLCursor, sql: str) -> None:
    """Execute a CREATE INDEX statement, ignoring duplicate-index errors."""
    try:
        cursor.execute(sql)
    except mysql.connector.Error as exc:
        # Error 1061: Duplicate key name — index already exists
        if exc.errno == 1061:
            pass
        else:
            raise


_TZ_VN = timezone(timedelta(hours=7))


def _now_iso() -> str:
    return datetime.now(tz=_TZ_VN).strftime("%Y-%m-%dT%H:%M:%S")


def _outcome_to_row(run_id: str, outcome: CrawlOutcome) -> tuple:
    """Convert a CrawlResult or ErrorResult into a DB insert tuple."""
    if isinstance(outcome, CrawlResult):
        return (
            run_id,
            outcome.sku,
            outcome.price,
            outcome.source,
            outcome.timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
            "OK",
            None,
        )
    # ErrorResult
    return (
        run_id,
        outcome.sku,
        None,
        outcome.source,
        outcome.timestamp.strftime("%Y-%m-%dT%H:%M:%S"),
        "Error",
        outcome.error,
    )
