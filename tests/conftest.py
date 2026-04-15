"""
Shared pytest fixtures for integration tests using testcontainers.

testcontainers automatically spins up a Docker MySQL container for the
test session — no MySQL installation or pre-configured URL required.

Usage:
    Tests that need a real database simply declare `repo` as a parameter:

        def test_something(repo):
            run_id = repo.start_run()
            ...

    The container starts once per session and each test gets a clean
    database via table truncation in teardown.
"""

from __future__ import annotations

import pytest
from testcontainers.mysql import MySqlContainer

from scraper.storage.database import ResultRepository

# MySQL image to use for tests — pin the major version for reproducibility
_MYSQL_IMAGE = "mysql:8.0"
_TEST_DATABASE = "price_crawler_test"
_TEST_USER = "crawler"
_TEST_PASSWORD = "crawler_test"


@pytest.fixture(scope="session")
def mysql_container():
    """Start a MySQL Docker container once for the entire test session.

    The container is stopped and removed automatically after all tests finish.
    Uses tmpfs-style ephemeral storage (container is discarded after session).
    """
    with MySqlContainer(
        image=_MYSQL_IMAGE,
        dbname=_TEST_DATABASE,
        username=_TEST_USER,
        password=_TEST_PASSWORD,
    ) as container:
        yield container


@pytest.fixture(scope="session")
def mysql_url(mysql_container: MySqlContainer) -> str:
    """Return a mysql:// URL pointing to the test container."""
    host = mysql_container.get_container_host_ip()
    port = mysql_container.get_exposed_port(3306)
    return (
        f"mysql://{_TEST_USER}:{_TEST_PASSWORD}"
        f"@{host}:{port}/{_TEST_DATABASE}"
    )


@pytest.fixture(scope="session")
def _base_repo(mysql_url: str) -> ResultRepository:
    """Create and initialise the schema once per session."""
    repo = ResultRepository(mysql_url)
    repo.init_schema()
    return repo


@pytest.fixture
def repo(_base_repo: ResultRepository) -> ResultRepository:
    """Provide a ResultRepository with a clean database for each test.

    Truncates all tables before each test so tests are fully isolated
    without needing to restart the container.
    """
    _truncate_tables(_base_repo)
    return _base_repo


# ── Private helpers ────────────────────────────────────────────────────────────

def _truncate_tables(repo: ResultRepository) -> None:
    """Delete all rows from crawl_results and crawl_runs."""
    with repo._connect() as conn:
        cursor = conn.cursor()
        # Disable FK checks so we can truncate in any order
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0;")
        cursor.execute("TRUNCATE TABLE crawl_results;")
        cursor.execute("TRUNCATE TABLE crawl_runs;")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1;")
        cursor.close()
