"""Real CDC environment fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import psycopg
import pytest
from psycopg import Connection

from ingestion.cdc.config import CdcSettings
from ingestion.cdc.connect_api import ConnectClient


@dataclass(frozen=True, slots=True)
class CdcEnvironment:
    settings: CdcSettings
    client: ConnectClient
    connection: Connection[Any]


@pytest.fixture(scope="session")
def cdc_environment() -> Iterator[CdcEnvironment]:
    if os.getenv("RUN_CDC_INTEGRATION") != "1":
        pytest.skip("Set RUN_CDC_INTEGRATION=1 and start Phase 4 services")
    settings = CdcSettings.from_env(os.environ)
    client = ConnectClient(settings)
    client.wait_ready()
    client.wait_running()
    database_url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not database_url:
        pytest.fail("TEST_DATABASE_URL or DATABASE_URL is required for CDC integration")
    try:
        connection = psycopg.connect(database_url, autocommit=True, connect_timeout=10)
    except psycopg.OperationalError as error:
        pytest.fail(f"PostgreSQL CDC source is unavailable: {error}")
    try:
        yield CdcEnvironment(settings, client, connection)
    finally:
        connection.close()
