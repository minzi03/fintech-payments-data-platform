"""Shared Portal API test fixtures."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from portal_api.adapters.registry import AdapterRegistry
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app
from portal_api.telemetry.metrics import InMemoryTelemetry


@pytest.fixture(scope="session", autouse=True)
def migrated_portal_security_schema() -> None:
    """Apply migrations only when the disposable Portal test database is configured."""
    url = os.environ.get("PORTAL_TEST_MIGRATION_DATABASE_URL", "")
    if not url:
        return
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    os.environ["PORTAL_MIGRATION_DATABASE_URL"] = url
    command.upgrade(config, "head")


@pytest.fixture
def settings() -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        service_version="0.1.0-test",
        build_sha="test-sha",
        build_time="2026-07-24T00:00:00Z",
        log_level="WARNING",
        log_format="json",
        allowed_origins="http://portal.test",
        trusted_hosts="testserver,portal.test",
        health_cache_ttl_seconds=0,
    )


@pytest.fixture
def telemetry() -> InMemoryTelemetry:
    return InMemoryTelemetry()


@pytest.fixture
def client(
    settings: PortalApiSettings,
    telemetry: InMemoryTelemetry,
) -> Iterator[TestClient]:
    app = create_app(
        settings=settings,
        adapter_registry=AdapterRegistry(),
        telemetry=telemetry,
    )
    with TestClient(app) as test_client:
        yield test_client
