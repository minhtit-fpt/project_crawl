"""Tests for security/env_loader.py — AppConfig loader and validator."""

import os
import pytest
from unittest.mock import patch

from security.env_loader import load_config, AppConfig

VALID_KEY_HEX = "a" * 64   # 32 bytes
VALID_IV_HEX  = "b" * 32   # 16 bytes

VALID_ENV = {
    "AES_SECRET_KEY": VALID_KEY_HEX,
    "AES_IV": VALID_IV_HEX,
}


def _load_with_env(extra: dict | None = None) -> AppConfig:
    env = {**VALID_ENV, **(extra or {})}
    with patch.dict(os.environ, env, clear=True):
        return load_config(env_path=".env.nonexistent")


class TestLoadConfigSuccess:
    def test_returns_app_config(self):
        config = _load_with_env()
        assert isinstance(config, AppConfig)

    def test_aes_key_decoded_correctly(self):
        config = _load_with_env()
        assert config.aes_secret_key == bytes.fromhex(VALID_KEY_HEX)
        assert len(config.aes_secret_key) == 32

    def test_aes_iv_decoded_correctly(self):
        config = _load_with_env()
        assert config.aes_iv == bytes.fromhex(VALID_IV_HEX)
        assert len(config.aes_iv) == 16

    def test_config_is_frozen(self):
        config = _load_with_env()
        with pytest.raises((AttributeError, TypeError)):
            config.log_level = "DEBUG"  # type: ignore

    def test_default_log_level_is_info(self):
        config = _load_with_env()
        assert config.log_level == "INFO"

    def test_custom_log_level(self):
        config = _load_with_env({"LOG_LEVEL": "debug"})
        assert config.log_level == "DEBUG"  # uppercased

    def test_empty_proxy_list(self):
        config = _load_with_env()
        assert config.proxy_list == []

    def test_proxy_list_parsed(self):
        config = _load_with_env({"PROXY_LIST": "http://p1:8080, http://p2:8080 "})
        assert config.proxy_list == ["http://p1:8080", "http://p2:8080"]

    def test_single_proxy(self):
        config = _load_with_env({"PROXY_LIST": "http://proxy:3128"})
        assert config.proxy_list == ["http://proxy:3128"]

    def test_database_url_none_when_absent(self):
        config = _load_with_env()
        assert config.database_url is None

    def test_database_url_set_when_present(self):
        config = _load_with_env({"DATABASE_URL": "postgresql://localhost/db"})
        assert config.database_url == "postgresql://localhost/db"

    def test_cms_api_url_none_when_absent(self):
        config = _load_with_env()
        assert config.cms_api_url is None

    def test_cms_api_token_none_when_absent(self):
        config = _load_with_env()
        assert config.cms_api_token is None

    def test_cms_api_url_set_when_present(self):
        config = _load_with_env({"CMS_API_URL": "https://cms.example.com/wp-json/v1/crawler"})
        assert config.cms_api_url == "https://cms.example.com/wp-json/v1/crawler"

    def test_cms_api_token_set_when_present(self):
        config = _load_with_env({"CMS_API_TOKEN": "Hoq2yMBjBL5"})
        assert config.cms_api_token == "Hoq2yMBjBL5"

    def test_cms_api_fields_not_in_required_keys(self):
        # CMS fields are optional — missing them must NOT raise EnvironmentError
        config = _load_with_env()
        assert config.cms_api_url is None
        assert config.cms_api_token is None


class TestLoadConfigMissingKeys:
    def test_missing_aes_key_raises(self):
        with patch.dict(os.environ, {"AES_IV": VALID_IV_HEX}, clear=True):
            with pytest.raises(EnvironmentError, match="AES_SECRET_KEY"):
                load_config(env_path=".env.nonexistent")

    def test_missing_aes_iv_raises(self):
        with patch.dict(os.environ, {"AES_SECRET_KEY": VALID_KEY_HEX}, clear=True):
            with pytest.raises(EnvironmentError, match="AES_IV"):
                load_config(env_path=".env.nonexistent")

    def test_missing_both_raises(self):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(EnvironmentError):
                load_config(env_path=".env.nonexistent")


class TestLoadConfigInvalidHex:
    def test_non_hex_key_raises(self):
        with patch.dict(os.environ, {**VALID_ENV, "AES_SECRET_KEY": "not-hex!"}, clear=True):
            with pytest.raises(EnvironmentError, match="not valid hex"):
                load_config(env_path=".env.nonexistent")

    def test_key_wrong_length_raises(self):
        # 30 bytes (60 hex chars) instead of 32
        short_key = "a" * 60
        with patch.dict(os.environ, {**VALID_ENV, "AES_SECRET_KEY": short_key}, clear=True):
            with pytest.raises(EnvironmentError, match="32 bytes"):
                load_config(env_path=".env.nonexistent")

    def test_iv_wrong_length_raises(self):
        # 8 bytes (16 hex chars) instead of 16
        short_iv = "b" * 16
        with patch.dict(os.environ, {**VALID_ENV, "AES_IV": short_iv}, clear=True):
            with pytest.raises(EnvironmentError, match="16 bytes"):
                load_config(env_path=".env.nonexistent")
