"""Shared Portal API test fixtures."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from portal_api.adapters.registry import AdapterRegistry
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app
from portal_api.telemetry.metrics import InMemoryTelemetry


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
