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


def _service(
    *adapters: FakeAdapter,
    timeout: float = 0.05,
    required_dependency_ids: tuple[str, ...] = (),
) -> HealthService:
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        dependency_timeout_seconds=timeout,
        health_cache_ttl_seconds=0,
    )
    return HealthService(
        AdapterRegistry(tuple(adapters)),
        settings,
        InMemoryTelemetry(),
        required_dependency_ids=required_dependency_ids,
    )


@pytest.mark.asyncio
async def test_empty_registry_is_not_ready() -> None:
    service = _service()
    dependencies = await service.dependency_summaries()

    assert dependencies == []
    assert service.readiness(dependencies) == (
        ReadinessStatus.NOT_READY,
        "No dependency readiness adapters are configured.",
    )


@pytest.mark.asyncio
async def test_missing_required_adapter_is_not_configured_and_not_ready() -> None:
    service = _service(required_dependency_ids=("required-database",))
    dependencies = await service.dependency_summaries()

    assert dependencies[0].dependency_id == "required-database"
    assert dependencies[0].required
    assert dependencies[0].status is DependencyStatus.NOT_CONFIGURED
    assert service.readiness(dependencies)[0] is ReadinessStatus.NOT_READY


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
async def test_required_adapter_exception_is_not_ready_and_logs_structured_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    logged: list[dict[str, object]] = []

    def capture_log(level: int, message: str, *, extra: dict[str, object]) -> None:
        del level, message
        logged.append(extra)

    monkeypatch.setattr("portal_api.health.service.LOGGER.log", capture_log)
    service = _service(
        FakeAdapter(
            "required-api",
            DependencyStatus.UP,
            required=True,
            raises=True,
        )
    )

    dependencies = await service.dependency_summaries()

    assert service.readiness(dependencies)[0] is ReadinessStatus.NOT_READY
    record = next(item for item in logged if item["event"] == "dependency_readiness_checked")
    assert record["dependency"] == "required-api"
    assert record["status"] == "UNAVAILABLE"
    assert float(record["latency_ms"]) >= 0
    assert record["failure_reason"] == "Dependency health check failed."
    assert record["timeout"] is False


@pytest.mark.asyncio
async def test_required_degraded_dependency_is_not_ready() -> None:
    service = _service(FakeAdapter("required-api", DependencyStatus.DEGRADED, required=True))
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


@pytest.mark.asyncio
async def test_readiness_recovers_after_required_dependency_recovers() -> None:
    adapter = FakeAdapter("required-api", DependencyStatus.UNAVAILABLE, required=True)
    service = _service(adapter)

    failed = await service.dependency_summaries(force=True)
    assert service.readiness(failed)[0] is ReadinessStatus.NOT_READY

    adapter._status = DependencyStatus.UP
    recovered = await service.dependency_summaries(force=True)
    assert service.readiness(recovered)[0] is ReadinessStatus.READY


def test_duplicate_adapter_registration_is_rejected() -> None:
    adapter = FakeAdapter("duplicate-api", DependencyStatus.UP)
    registry = AdapterRegistry((adapter,))

    with pytest.raises(ValueError, match="already registered"):
        registry.register(adapter)
