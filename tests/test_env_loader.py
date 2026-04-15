"""Tests for security/env_loader.py — AppConfig loader and validator."""

import os
import pytest
from unittest.mock import patch

from security.env_loader import load_config, AppConfig, _DEFAULT_DATABASE_URL

VALID_ENV: dict[str, str] = {}   # no required keys anymore


def _load_with_env(extra: dict | None = None) -> AppConfig:
    env = {**(extra or {})}
    with patch.dict(os.environ, env, clear=True):
        return load_config(env_path=".env.nonexistent")


class TestLoadConfigSuccess:
    def test_returns_app_config(self):
        config = _load_with_env()
        assert isinstance(config, AppConfig)

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

    def test_database_url_default(self):
        """When DATABASE_URL is absent, falls back to the default MySQL URL."""
        config = _load_with_env()
        assert config.database_url == _DEFAULT_DATABASE_URL

    def test_database_url_custom_mysql(self):
        url = "mysql://user:pass@myhost:3306/mydb"
        config = _load_with_env({"DATABASE_URL": url})
        assert config.database_url == url

    def test_database_url_mysqlconnector_scheme(self):
        url = "mysql+mysqlconnector://user:pass@myhost:3306/mydb"
        config = _load_with_env({"DATABASE_URL": url})
        assert config.database_url == url

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

    def test_pull_api_host_default(self):
        config = _load_with_env()
        assert config.pull_api_host == "0.0.0.0"

    def test_pull_api_port_default(self):
        config = _load_with_env()
        assert config.pull_api_port == 8080

    def test_pull_api_port_custom(self):
        config = _load_with_env({"PULL_API_PORT": "9090"})
        assert config.pull_api_port == 9090

    def test_pull_api_token_none_when_absent(self):
        config = _load_with_env()
        assert config.pull_api_token is None

    def test_pull_api_token_set_when_present(self):
        config = _load_with_env({"PULL_API_TOKEN": "mysecret"})
        assert config.pull_api_token == "mysecret"

    def test_no_sqlite_db_path_field(self):
        """AppConfig must no longer have a sqlite_db_path field."""
        config = _load_with_env()
        assert not hasattr(config, "sqlite_db_path")


class TestDatabaseUrlValidation:
    def test_invalid_scheme_raises(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/db"}, clear=True):
            with pytest.raises(EnvironmentError, match="mysql"):
                load_config(env_path=".env.nonexistent")

    def test_sqlite_scheme_raises(self):
        with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///data/test.db"}, clear=True):
            with pytest.raises(EnvironmentError, match="mysql"):
                load_config(env_path=".env.nonexistent")

    def test_http_scheme_raises(self):
        with patch.dict(os.environ, {"DATABASE_URL": "http://localhost/db"}, clear=True):
            with pytest.raises(EnvironmentError, match="mysql"):
                load_config(env_path=".env.nonexistent")


class TestLoadConfigPortValidation:
    def test_non_integer_port_raises(self):
        with patch.dict(os.environ, {"PULL_API_PORT": "abc"}, clear=True):
            with pytest.raises(EnvironmentError, match="integer"):
                load_config(env_path=".env.nonexistent")

    def test_port_zero_raises(self):
        with patch.dict(os.environ, {"PULL_API_PORT": "0"}, clear=True):
            with pytest.raises(EnvironmentError, match="65535"):
                load_config(env_path=".env.nonexistent")

    def test_port_too_large_raises(self):
        with patch.dict(os.environ, {"PULL_API_PORT": "99999"}, clear=True):
            with pytest.raises(EnvironmentError, match="65535"):
                load_config(env_path=".env.nonexistent")
