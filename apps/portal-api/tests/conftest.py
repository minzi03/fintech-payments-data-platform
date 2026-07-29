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
from portal_test_support.disposable_database import (
    DestructiveOperationGrant,
    DisposableDatabaseError,
    DisposableDatabaseIdentity,
    PostgresDisposableDatabaseProbe,
    consume_destructive_grant,
)

_DATABASE_URL_VARIABLES = (
    "PORTAL_TEST_MIGRATION_DATABASE_URL",
    "PORTAL_TEST_RUNTIME_DATABASE_URL",
    "PORTAL_TEST_ARCHIVE_DATABASE_URL",
)


@pytest.fixture(scope="session", autouse=True)
def disposable_database_grant() -> Iterator[DestructiveOperationGrant | None]:
    """Consume orchestration proof before any Portal test mutates PostgreSQL."""

    configured_urls = {name: os.environ.get(name, "") for name in _DATABASE_URL_VARIABLES}
    if not any(configured_urls.values()):
        yield None
        return
    if not all(configured_urls.values()):
        pytest.fail("DISPOSABLE_URL_SET_INCOMPLETE: all role-specific test URLs are required")

    mode = os.environ.get("PORTAL_TEST_DATABASE_MODE", "")
    purpose_by_mode = {
        "orchestrated-integration-v1": "portal-integration-validation",
        "orchestrated-migration-v1": "portal-migration-validation",
    }
    purpose = purpose_by_mode.get(mode)
    if purpose is None:
        pytest.fail(
            "DISPOSABLE_MODE_REQUIRED: database tests require the explicit test orchestrator"
        )

    token = os.environ.pop("PORTAL_TEST_DESTRUCTIVE_TOKEN", "")
    identity = DisposableDatabaseIdentity(
        run_id=os.environ.get("PORTAL_TEST_RUN_ID", ""),
        compose_project=os.environ.get("PORTAL_TEST_COMPOSE_PROJECT", ""),
        database_name=os.environ.get("PORTAL_TEST_DATABASE_NAME", ""),
        purpose=purpose,
        migration_url=configured_urls["PORTAL_TEST_MIGRATION_DATABASE_URL"],
        runtime_url=configured_urls["PORTAL_TEST_RUNTIME_DATABASE_URL"],
        archive_url=configured_urls["PORTAL_TEST_ARCHIVE_DATABASE_URL"],
    )
    try:
        grant = consume_destructive_grant(
            identity,
            token,
            PostgresDisposableDatabaseProbe(),
            environment=os.environ,
        )
    except DisposableDatabaseError as error:
        pytest.fail(str(error))

    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    os.environ["PORTAL_MIGRATION_DATABASE_URL"] = identity.migration_url
    command.upgrade(config, "head")
    yield grant


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
