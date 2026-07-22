"""PostgreSQL integration fixtures that are opt-in through environment configuration."""

from __future__ import annotations

import os
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from psycopg import Connection


@pytest.fixture
def postgres_connection() -> Iterator[Connection[Any]]:
    """Yield a rollback-only connection when an integration database is configured."""
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL or DATABASE_URL to run PostgreSQL integration tests")

    try:
        connection = psycopg.connect(database_url, autocommit=False, connect_timeout=10)
    except psycopg.OperationalError as error:
        pytest.fail(f"Configured PostgreSQL integration database is unavailable: {error}")

    try:
        yield connection
    finally:
        if not connection.closed:
            connection.rollback()
            connection.close()
