"""
Terminal output formatter.

Accepts a list of CrawlResult / ErrorResult objects and prints a formatted
table to stdout.

Output columns:
  SKU | Price | Source | Timestamp (UTC) | Status

Usage:
    from scraper.output import print_results
    from scraper.scheduler import CrawlResult, ErrorResult

    print_results(results)
"""

from __future__ import annotations

import sys
from datetime import datetime

from scraper.scheduler import CrawlOutcome, CrawlResult, ErrorResult

# Column headers
_HEADERS = ["SKU", "Price", "Source", "Timestamp (UTC)", "Status"]

# Column widths (minimum)
_COL_WIDTHS = [20, 15, 20, 25, 30]


def print_results(
    results: list[CrawlOutcome],
    file=None,
) -> None:
    """Print all crawl results as a formatted table to stdout (or file).

    Args:
        results: List of CrawlResult / ErrorResult objects.
        file:    Output stream (default: sys.stdout). Override in tests.
    """
    if file is None:
        file = sys.stdout

    if not results:
        print("No results to display.", file=file)
        return

    rows = [_format_row(r) for r in results]
    _print_table(rows, file=file)

    ok_count = sum(1 for r in results if isinstance(r, CrawlResult))
    err_count = len(results) - ok_count
    print(f"\nTotal: {len(results)}  |  OK: {ok_count}  |  Errors: {err_count}", file=file)


def format_results(results: list[CrawlOutcome]) -> str:
    """Return formatted table as a string (useful for logging or file output)."""
    import io
    buf = io.StringIO()
    print_results(results, file=buf)
    return buf.getvalue()


# ── Private helpers ────────────────────────────────────────────────────────────

def _format_row(result: CrawlOutcome) -> list[str]:
    """Convert a CrawlResult or ErrorResult into a list of column strings."""
    if isinstance(result, CrawlResult):
        return [
            result.sku,
            f"{result.price:.2f}",
            result.source,
            _fmt_timestamp(result.timestamp),
            result.status,
        ]
    return [
        result.sku,
        "—",
        result.source,
        _fmt_timestamp(result.timestamp),
        f"Error: {result.error}",
    ]


def _fmt_timestamp(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _print_table(rows: list[list[str]], file) -> None:
    """Print a simple aligned table with a header and separator line."""
    widths = list(_COL_WIDTHS)
    for i, header in enumerate(_HEADERS):
        widths[i] = max(widths[i], len(header))
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    fmt = "  ".join(f"{{:<{w}}}" for w in widths)
    separator = "  ".join("-" * w for w in widths)

    print(fmt.format(*_HEADERS), file=file)
    print(separator, file=file)
    for row in rows:
        print(fmt.format(*row), file=file)
