"""
Environment variable loader and validator.

Loads .env at startup, validates all required keys are present and correctly
formatted, then returns a frozen config dataclass. Raises immediately with a
clear message if anything is missing or malformed — fail fast before crawling
begins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from dotenv import load_dotenv


REQUIRED_KEYS = ("AES_SECRET_KEY", "AES_IV")

AES_KEY_BYTES = 32   # 256-bit key → 64 hex chars
AES_IV_BYTES = 16    # 128-bit IV  → 32 hex chars


_DEFAULT_SQLITE_PATH = "data/crawl_results.db"
_DEFAULT_API_HOST = "0.0.0.0"
_DEFAULT_API_PORT = 8080


@dataclass(frozen=True)
class AppConfig:
    aes_secret_key: bytes
    aes_iv: bytes
    proxy_list: list[str]
    log_level: str
    database_url: Optional[str]
    cms_api_url: Optional[str]
    cms_api_token: Optional[str]
    # Pull API / local storage
    sqlite_db_path: str
    pull_api_host: str
    pull_api_port: int
    pull_api_token: Optional[str]


def load_config(env_path: str = ".env") -> AppConfig:
    """Load and validate environment variables, returning a frozen AppConfig.

    Args:
        env_path: Path to the .env file (default: ".env" in cwd).

    Returns:
        AppConfig with all validated values.

    Raises:
        EnvironmentError: If any required variable is missing or malformed.
    """
    load_dotenv(dotenv_path=env_path, override=False)

    _assert_required_keys_present()

    aes_key = _load_hex_bytes("AES_SECRET_KEY", expected_bytes=AES_KEY_BYTES)
    aes_iv = _load_hex_bytes("AES_IV", expected_bytes=AES_IV_BYTES)
    proxy_list = _load_proxy_list()
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    database_url = os.getenv("DATABASE_URL") or None
    cms_api_url = os.getenv("CMS_API_URL") or None
    cms_api_token = os.getenv("CMS_API_TOKEN") or None
    sqlite_db_path = os.getenv("SQLITE_DB_PATH", _DEFAULT_SQLITE_PATH)
    pull_api_host = os.getenv("PULL_API_HOST", _DEFAULT_API_HOST)
    pull_api_port = _load_port("PULL_API_PORT", default=_DEFAULT_API_PORT)
    pull_api_token = os.getenv("PULL_API_TOKEN") or None

    return AppConfig(
        aes_secret_key=aes_key,
        aes_iv=aes_iv,
        proxy_list=proxy_list,
        log_level=log_level,
        database_url=database_url,
        cms_api_url=cms_api_url,
        cms_api_token=cms_api_token,
        sqlite_db_path=sqlite_db_path,
        pull_api_host=pull_api_host,
        pull_api_port=pull_api_port,
        pull_api_token=pull_api_token,
    )


# ── Private helpers ────────────────────────────────────────────────────────────

def _assert_required_keys_present() -> None:
    missing = [key for key in REQUIRED_KEYS if not os.getenv(key)]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variables: {', '.join(missing)}\n"
            f"Copy .env.example to .env and fill in the values."
        )


def _load_hex_bytes(key: str, expected_bytes: int) -> bytes:
    raw = os.getenv(key, "").strip()

    try:
        decoded = bytes.fromhex(raw)
    except ValueError:
        raise EnvironmentError(
            f"Environment variable '{key}' is not valid hex. "
            f"Expected {expected_bytes * 2} hex characters."
        )

    if len(decoded) != expected_bytes:
        raise EnvironmentError(
            f"Environment variable '{key}' must be {expected_bytes} bytes "
            f"({expected_bytes * 2} hex chars). Got {len(decoded)} bytes."
        )

    return decoded


def _load_port(key: str, default: int) -> int:
    raw = os.getenv(key, "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        raise EnvironmentError(
            f"Environment variable '{key}' must be an integer port number. Got: {raw!r}"
        )
    if not (1 <= port <= 65535):
        raise EnvironmentError(
            f"Environment variable '{key}' must be between 1 and 65535. Got: {port}"
        )
    return port


def _load_proxy_list() -> list[str]:
    raw = os.getenv("PROXY_LIST", "").strip()
    if not raw:
        return []
    return [proxy.strip() for proxy in raw.split(",") if proxy.strip()]
