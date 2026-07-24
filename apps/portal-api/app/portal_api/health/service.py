"""Bounded dependency checks and readiness aggregation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from time import monotonic, perf_counter

from portal_api.adapters.models import AdapterHealthResult, DependencyStatus, HealthAdapter
from portal_api.adapters.registry import AdapterRegistry
from portal_api.core.config import PortalApiSettings
from portal_api.health.models import DependencySummary, ReadinessStatus
from portal_api.telemetry.metrics import TelemetryRecorder


class HealthService:
    """Aggregate dependency state without making liveness infrastructure-dependent."""

    def __init__(
        self,
        registry: AdapterRegistry,
        settings: PortalApiSettings,
        telemetry: TelemetryRecorder,
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._telemetry = telemetry
        self._cached_at = 0.0
        self._cached: tuple[DependencySummary, ...] = ()
        self._cache_lock = asyncio.Lock()

    async def dependency_summaries(self, *, force: bool = False) -> list[DependencySummary]:
        now = monotonic()
        if (
            not force
            and self._cached_at > 0
            and now - self._cached_at <= self._settings.health_cache_ttl_seconds
        ):
            return list(self._cached)

        async with self._cache_lock:
            now = monotonic()
            if (
                not force
                and self._cached_at > 0
                and now - self._cached_at <= self._settings.health_cache_ttl_seconds
            ):
                return list(self._cached)
            adapters = self._registry.all()
            if not adapters:
                self._cached = ()
                self._cached_at = monotonic()
                return []
            results = await asyncio.gather(*(self._check(adapter) for adapter in adapters))
            self._cached = tuple(results)
            self._cached_at = monotonic()
            return list(self._cached)

    async def _check(self, adapter: HealthAdapter) -> DependencySummary:
        identity = adapter.identity
        started = perf_counter()
        try:
            result: AdapterHealthResult = await asyncio.wait_for(
                adapter.check_health(),
                timeout=self._settings.dependency_timeout_seconds,
            )
            status = result.status
            reason = result.reason
            observed_at = result.observed_at
        except TimeoutError:
            status = DependencyStatus.TIMEOUT
            reason = "Dependency health check timed out."
            observed_at = datetime.now(UTC)
        except Exception:
            status = DependencyStatus.UNAVAILABLE
            reason = "Dependency health check failed."
            observed_at = datetime.now(UTC)
        duration_ms = (perf_counter() - started) * 1000
        self._telemetry.record_dependency(identity.adapter_id, status.value, duration_ms)
        return DependencySummary(
            dependency_id=identity.adapter_id,
            display_name=identity.display_name,
            dependency_type=identity.dependency_type,
            required=identity.required,
            status=status,
            observed_at=observed_at,
            latency_ms=round(duration_ms, 3),
            reason=reason,
            runbook_url=identity.runbook_url,
            adapter_version=identity.version,
        )

    def readiness(self, dependencies: list[DependencySummary]) -> tuple[ReadinessStatus, str]:
        blocking = {
            DependencyStatus.UNAVAILABLE,
            DependencyStatus.TIMEOUT,
            DependencyStatus.NOT_CONFIGURED,
        }
        if any(item.required and item.status in blocking for item in dependencies):
            return ReadinessStatus.NOT_READY, "One or more required dependencies are unavailable."
        if any(item.status is not DependencyStatus.UP for item in dependencies):
            return ReadinessStatus.DEGRADED, "Optional or degraded dependencies require attention."
        return ReadinessStatus.READY, "Portal foundation capabilities are ready."
