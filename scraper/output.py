"""
Terminal output formatter.

Accepts a list of CrawlResult / ErrorResult and prints a formatted table
to stdout. Optionally encrypts SKU and Price columns when an Encryptor is
supplied (--encrypt flag).

Output columns: SKU | Price | Source | Timestamp (UTC) | Status
"""

from __future__ import annotations

import io
import sys
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from scraper.scheduler import CrawlOutcome, CrawlResult, ErrorResult

if TYPE_CHECKING:
    from security.encryption import Encryptor

_HEADERS = ["SKU", "Price", "Source", "Timestamp (UTC)", "Status"]
_COL_WIDTHS = [20, 15, 20, 25, 30]


def print_results(
    results: list[CrawlOutcome],
    encryptor: Optional["Encryptor"] = None,
    file=None,
) -> None:
    """Print all crawl results as a formatted table.

    Args:
        results:   List of CrawlResult / ErrorResult objects.
        encryptor: If provided, SKU and Price columns are AES-encrypted.
        file:      Output stream (default: sys.stdout).
    """
    if file is None:
        file = sys.stdout

    if not results:
        print("No results to display.", file=file)
        return

    rows = [_format_row(r, encryptor) for r in results]
    _print_table(rows, file=file)

    ok_count = sum(1 for r in results if isinstance(r, CrawlResult))
    err_count = len(results) - ok_count
    print(f"\nTotal: {len(results)}  |  OK: {ok_count}  |  Errors: {err_count}", file=file)


def format_results(
    results: list[CrawlOutcome],
    encryptor: Optional["Encryptor"] = None,
) -> str:
    """Return the formatted table as a string."""
    buf = io.StringIO()
    print_results(results, encryptor=encryptor, file=buf)
    return buf.getvalue()


# ── Private helpers ────────────────────────────────────────────────────────────

def _format_row(result: CrawlOutcome, encryptor: Optional["Encryptor"]) -> list[str]:
    if isinstance(result, CrawlResult):
        sku = result.sku
        price = f"{result.price:.2f}"
        source = result.source
        timestamp = _fmt_timestamp(result.timestamp)
        status = result.status
    else:
        sku = result.sku
        price = "\u2014"   # em dash
        source = result.source
        timestamp = _fmt_timestamp(result.timestamp)
        status = f"Error: {result.error}"

    if encryptor is not None:
        sku = encryptor.encrypt(sku)
        if price != "\u2014":
            price = encryptor.encrypt(price)

    return [sku, price, source, timestamp, status]


def _fmt_timestamp(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S")


def _print_table(rows: list[list[str]], file) -> None:
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
