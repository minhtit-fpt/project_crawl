"""
Configuration loader for the Price Crawler.

Reads scraper/config/sites.yaml, validates the schema, and exposes:
  - SCRAPY_SETTINGS   dict ready to pass into Scrapy's CrawlerProcess
  - load_sites()      returns a list of validated SiteConfig dataclasses
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import yaml


# ── Dataclasses ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SelectorConfig:
    price_css: str
    price_xpath: str


@dataclass(frozen=True)
class SiteConfig:
    name: str
    base_url: str
    skus: list[str]
    selectors: SelectorConfig
    requires_js: bool
    rate_limit_seconds: float


# ── Scrapy base settings ───────────────────────────────────────────────────────

SCRAPY_SETTINGS: dict = {
    "BOT_NAME": "project_claw",
    "USER_AGENT": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "ROBOTSTXT_OBEY": False,
    "CONCURRENT_REQUESTS": 4,
    "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
    "DOWNLOAD_TIMEOUT": 30,
    "RETRY_ENABLED": False,           # Retries are handled by retry.py, not Scrapy
    "LOG_LEVEL": os.getenv("LOG_LEVEL", "INFO"),
    # scrapy-playwright
    "DOWNLOAD_HANDLERS": {
        "http": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
    },
    "PLAYWRIGHT_BROWSER_TYPE": "chromium",
    "PLAYWRIGHT_LAUNCH_OPTIONS": {
        "headless": True,
        "args": [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ],
    },
    "TWISTED_REACTOR": "twisted.internet.asyncioreactor.AsyncioSelectorReactor",
}


# ── Public loader ──────────────────────────────────────────────────────────────

_DEFAULT_YAML = Path(__file__).parent / "sites.yaml"


def load_sites(config_path: Optional[str] = None) -> list[SiteConfig]:
    """Parse and validate sites.yaml, returning a list of SiteConfig objects.

    Args:
        config_path: Path to sites.yaml. Defaults to scraper/config/sites.yaml.

    Returns:
        List of validated SiteConfig instances.

    Raises:
        FileNotFoundError: If the YAML file does not exist.
        ValueError: If a site entry fails validation.
    """
    path = Path(config_path) if config_path else _DEFAULT_YAML

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with path.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    if not raw or "sites" not in raw:
        raise ValueError(f"sites.yaml must contain a top-level 'sites' key: {path}")

    return [_parse_site(entry, idx) for idx, entry in enumerate(raw["sites"])]


# ── Private helpers ────────────────────────────────────────────────────────────

def _parse_site(entry: dict, idx: int) -> SiteConfig:
    label = f"sites[{idx}]"

    name = _require_str(entry, "name", label)
    base_url = _require_str(entry, "base_url", label)
    skus = _require_list(entry, "skus", label)
    selectors_raw = entry.get("selectors")
    requires_js = bool(entry.get("requires_js", False))
    rate_limit = float(entry.get("rate_limit_seconds", 1.0))

    _validate_url(base_url, label)
    if not skus:
        raise ValueError(f"{label}: 'skus' list must not be empty")
    if "{sku}" not in base_url:
        raise ValueError(f"{label}: 'base_url' must contain '{{sku}}' placeholder")

    selectors = _parse_selectors(selectors_raw, label)

    return SiteConfig(
        name=name,
        base_url=base_url,
        skus=[str(s) for s in skus],
        selectors=selectors,
        requires_js=requires_js,
        rate_limit_seconds=rate_limit,
    )


def _parse_selectors(raw: object, label: str) -> SelectorConfig:
    if not isinstance(raw, dict):
        raise ValueError(f"{label}: 'selectors' must be a mapping with 'price_css' and 'price_xpath'")
    css = _require_str(raw, "price_css", f"{label}.selectors")
    xpath = _require_str(raw, "price_xpath", f"{label}.selectors")
    return SelectorConfig(price_css=css, price_xpath=xpath)


def _require_str(d: dict, key: str, label: str) -> str:
    val = d.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"{label}: '{key}' must be a non-empty string")
    return val.strip()


def _require_list(d: dict, key: str, label: str) -> list:
    val = d.get(key)
    if not isinstance(val, list):
        raise ValueError(f"{label}: '{key}' must be a list")
    return val


def _validate_url(url: str, label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"{label}: 'base_url' must start with http:// or https://")
    if not parsed.netloc:
        raise ValueError(f"{label}: 'base_url' has no host")
