"""Bounded dependency checks and readiness aggregation."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic, perf_counter

from opentelemetry.trace import SpanKind

from portal_api.adapters.models import AdapterHealthResult, DependencyStatus, HealthAdapter
from portal_api.adapters.registry import AdapterRegistry
from portal_api.core.config import PortalApiSettings
from portal_api.health.models import DependencySummary, ReadinessStatus
from portal_api.telemetry.metrics import TelemetryRecorder

LOGGER = logging.getLogger("portal_api.readiness")


class HealthService:
    """Aggregate dependency state without making liveness infrastructure-dependent."""

    def __init__(
        self,
        registry: AdapterRegistry,
        settings: PortalApiSettings,
        telemetry: TelemetryRecorder,
        required_dependency_ids: tuple[str, ...] = (),
    ) -> None:
        self._registry = registry
        self._settings = settings
        self._telemetry = telemetry
        self._required_dependency_ids = required_dependency_ids
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
            results = {
                summary.dependency_id: summary
                for summary in await asyncio.gather(
                    *(self._check(adapter) for adapter in self._registry.all())
                )
            }
            for dependency_id in self._registry.missing_required(self._required_dependency_ids):
                summary = DependencySummary(
                    dependency_id=dependency_id,
                    display_name=dependency_id,
                    dependency_type="required-runtime",
                    required=True,
                    status=DependencyStatus.NOT_CONFIGURED,
                    observed_at=datetime.now(UTC),
                    latency_ms=0,
                    reason="Required dependency adapter is not configured.",
                    adapter_version="not-configured",
                )
                results[dependency_id] = summary
                self._telemetry.record_dependency(
                    dependency_id,
                    DependencyStatus.NOT_CONFIGURED.value,
                    0,
                )
                self._log_result(summary)
            self._cached = tuple(results[key] for key in sorted(results))
            self._cached_at = monotonic()
            return list(self._cached)

    async def _check(self, adapter: HealthAdapter) -> DependencySummary:
        identity = adapter.identity
        started = perf_counter()
        try:
            result: AdapterHealthResult = await asyncio.wait_for(
                self._execute_check(adapter),
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
        summary = DependencySummary(
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
        self._log_result(summary)
        return summary

    async def _execute_check(self, adapter: HealthAdapter) -> AdapterHealthResult:
        identity = adapter.identity
        with self._telemetry.span(
            "readiness.dependency",
            kind=SpanKind.CLIENT,
            attributes={
                "dependency": identity.adapter_id,
                "dependency.type": identity.dependency_type,
                "dependency.required": identity.required,
            },
        ):
            return await adapter.check_health()

    def readiness(self, dependencies: list[DependencySummary]) -> tuple[ReadinessStatus, str]:
        if not dependencies:
            return ReadinessStatus.NOT_READY, "No dependency readiness adapters are configured."
        if any(item.required and item.status is not DependencyStatus.UP for item in dependencies):
            return ReadinessStatus.NOT_READY, "One or more required dependencies are unavailable."
        if any(item.status is not DependencyStatus.UP for item in dependencies):
            return ReadinessStatus.DEGRADED, "Optional or degraded dependencies require attention."
        return ReadinessStatus.READY, "Portal foundation capabilities are ready."

    @staticmethod
    def _log_result(summary: DependencySummary) -> None:
        LOGGER.log(
            logging.INFO if summary.status is DependencyStatus.UP else logging.WARNING,
            "dependency readiness checked",
            extra={
                "event": "dependency_readiness_checked",
                "dependency": summary.dependency_id,
                "status": summary.status.value,
                "latency_ms": summary.latency_ms,
                "failure_reason": summary.reason,
                "timeout": summary.status is DependencyStatus.TIMEOUT,
            },
        )
