"""Readiness aggregation and adapter isolation tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from portal_api.adapters.models import (
    AdapterHealthResult,
    AdapterIdentity,
    DependencyStatus,
)
from portal_api.adapters.registry import AdapterRegistry
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.health.models import ReadinessStatus
from portal_api.health.service import HealthService
from portal_api.telemetry.metrics import InMemoryTelemetry


class FakeAdapter:
    def __init__(
        self,
        adapter_id: str,
        status: DependencyStatus,
        *,
        required: bool = False,
        delay: float = 0,
        raises: bool = False,
    ) -> None:
        self.identity = AdapterIdentity(
            adapter_id=adapter_id,
            display_name=adapter_id.title(),
            dependency_type="test",
            required=required,
            version="test-v1",
        )
        self._status = status
        self._delay = delay
        self._raises = raises

    async def check_health(self) -> AdapterHealthResult:
        await asyncio.sleep(self._delay)
        if self._raises:
            raise RuntimeError("password=must-not-leak")
        return AdapterHealthResult(
            identity=self.identity,
            status=self._status,
            observed_at=datetime.now(UTC),
            reason="Test state",
        )


def _service(*adapters: FakeAdapter, timeout: float = 0.05) -> HealthService:
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        dependency_timeout_seconds=timeout,
        health_cache_ttl_seconds=0,
    )
    return HealthService(AdapterRegistry(tuple(adapters)), settings, InMemoryTelemetry())


@pytest.mark.asyncio
async def test_empty_registry_is_truthfully_ready() -> None:
    service = _service()
    dependencies = await service.dependency_summaries()

    assert dependencies == []
    assert service.readiness(dependencies)[0] is ReadinessStatus.READY


@pytest.mark.asyncio
async def test_optional_failure_degrades_without_blocking() -> None:
    service = _service(FakeAdapter("optional-api", DependencyStatus.UP, raises=True))
    dependencies = await service.dependency_summaries()

    assert dependencies[0].status is DependencyStatus.UNAVAILABLE
    assert dependencies[0].reason == "Dependency health check failed."
    assert "must-not-leak" not in str(dependencies[0])
    assert service.readiness(dependencies)[0] is ReadinessStatus.DEGRADED


@pytest.mark.asyncio
async def test_required_failure_is_not_ready() -> None:
    service = _service(FakeAdapter("required-api", DependencyStatus.UNAVAILABLE, required=True))
    dependencies = await service.dependency_summaries()

    assert service.readiness(dependencies)[0] is ReadinessStatus.NOT_READY


@pytest.mark.asyncio
async def test_adapter_timeout_is_bounded_and_sanitized() -> None:
    service = _service(
        FakeAdapter("slow-api", DependencyStatus.UP, delay=0.1),
        timeout=0.01,
    )
    dependencies = await service.dependency_summaries()

    assert dependencies[0].status is DependencyStatus.TIMEOUT
    assert dependencies[0].reason == "Dependency health check timed out."
