"""Policy orchestration, degradation behavior, and provider quotas."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from math import ceil
from time import monotonic, perf_counter
from uuid import uuid4

from portal_api.abuse.audit import AbuseAuditSink
from portal_api.abuse.client_address import TrustedClientAddressResolver
from portal_api.abuse.models import (
    AbuseBackendStatus,
    AbuseBucketRequest,
    AbuseConcurrencyLease,
    AbuseConcurrencyRequest,
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationRequest,
    AbuseEvaluationResult,
    AbuseFailureMode,
    AbuseOperation,
    PenaltyLevel,
)
from portal_api.abuse.policy import AbusePolicyRegistry
from portal_api.abuse.ports import (
    AbuseBackendTimeout,
    AbuseBackendUnavailable,
    AbuseEnforcementPort,
)
from portal_api.core.config import PortalApiSettings
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder

LOGGER = logging.getLogger("portal_api.abuse")
_KEY_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9:_.-]{0,63}$")


class AbuseProtectionService:
    """Apply distributed policies while preserving operation-specific authority boundaries."""

    def __init__(
        self,
        *,
        settings: PortalApiSettings,
        resolver: TrustedClientAddressResolver,
        policies: AbusePolicyRegistry,
        distributed_store: AbuseEnforcementPort,
        local_fallback: AbuseEnforcementPort,
        telemetry: TelemetryRecorder | None = None,
        audit_sink: AbuseAuditSink | None = None,
    ) -> None:
        if _KEY_PREFIX.fullmatch(settings.redis_key_prefix) is None:
            raise ValueError("Redis key prefix must be a bounded safe identifier")
        self._settings = settings
        self._resolver = resolver
        self._policies = policies
        self._distributed = distributed_store
        self._fallback = local_fallback
        self._telemetry = telemetry or NoopTelemetry()
        self._audit = audit_sink
        self._last_backend_audit: dict[AbuseOperation, float] = {}
        self._backend_audit_lock = asyncio.Lock()

    @property
    def resolver(self) -> TrustedClientAddressResolver:
        return self._resolver

    def fingerprint(self, dimension: AbuseDimension, value: str | None) -> str | None:
        if value is None or not value:
            return None
        return self._resolver.fingerprint_value(dimension.value, value)

    async def evaluate(
        self,
        operation: AbuseOperation,
        *,
        dimensions: Mapping[AbuseDimension, str | None],
        correlation_id: str,
        request_id: str,
    ) -> AbuseEvaluationResult:
        policy = self._policies.get(operation)
        safe_dimensions: dict[AbuseDimension, str | None] = {
            dimension: self.fingerprint(dimension, value)
            for dimension, value in dimensions.items()
            if dimension is not AbuseDimension.GLOBAL_OPERATION
        }
        safe_dimensions[AbuseDimension.GLOBAL_OPERATION] = "global"
        buckets: list[AbuseBucketRequest] = []
        for item in policy.dimensions:
            fingerprint = safe_dimensions.get(item.dimension)
            if fingerprint is None:
                continue
            bucket = item.bucket
            buckets.append(
                AbuseBucketRequest(
                    key=self._bucket_key(
                        operation=operation,
                        policy_name=policy.name,
                        dimension=item.dimension,
                        fingerprint=fingerprint,
                    ),
                    dimension=item.dimension,
                    capacity=bucket.capacity,
                    refill_tokens=bucket.refill_tokens,
                    refill_period_ms=max(1, int(bucket.refill_period_seconds * 1000)),
                    request_cost=bucket.request_cost,
                    state_ttl_ms=bucket.state_ttl_seconds * 1000,
                    penalty_base_ms=policy.penalty.base_block_seconds * 1000,
                    penalty_max_level=policy.penalty.max_level,
                    penalty_decay_ms=policy.penalty.decay_seconds * 1000,
                )
            )
        if not buckets:
            raise ValueError(f"No applicable abuse dimensions for {operation.value}")
        request = AbuseEvaluationRequest(
            operation=operation,
            policy_name=policy.name,
            policy_version=self._settings.abuse_policy_version,
            buckets=tuple(buckets),
        )
        started = perf_counter()
        fallback_active = False
        try:
            with (
                self._telemetry.span(
                    "abuse.policy.evaluate",
                    attributes={
                        "abuse.operation": operation.value,
                        "abuse.policy": policy.name,
                        "abuse.policy_version": self._settings.abuse_policy_version,
                    },
                ),
                self._telemetry.span(
                    "abuse.redis.enforce",
                    attributes={
                        "abuse.operation": operation.value,
                        "abuse.policy": policy.name,
                    },
                ),
            ):
                result = await self._distributed.evaluate(request)
        except (AbuseBackendTimeout, AbuseBackendUnavailable) as error:
            fallback_active = True
            result = await self._degraded_result(
                request=request,
                failure_mode=policy.backend_failure_mode,
                timeout=isinstance(error, AbuseBackendTimeout),
            )
            await self._audit_backend_transition(
                operation=operation,
                result=result,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        duration_ms = (perf_counter() - started) * 1000
        self._telemetry.record_abuse(
            operation=operation.value,
            decision=result.decision.value,
            policy=policy.name,
            policy_version=self._settings.abuse_policy_version,
            dimension=(
                result.limiting_dimension.value if result.limiting_dimension is not None else "none"
            ),
            backend_status=result.backend_status.value,
            failure_class=(
                "backend_unavailable"
                if result.backend_status is not AbuseBackendStatus.UP
                else "none"
            ),
            penalty_level=result.penalty_level.value,
            fallback_mode="active" if fallback_active else "inactive",
            duration_ms=duration_ms,
        )
        LOGGER.info(
            "abuse policy evaluated",
            extra={
                "event": "abuse_policy_evaluated",
                "operation": operation.value,
                "decision": result.decision.value,
                "policy": policy.name,
                "backend_status": result.backend_status.value,
                "retry_after_seconds": result.retry_after_seconds,
                "penalty_level": result.penalty_level.value,
                "correlation_id": correlation_id,
            },
        )
        if result.decision in {
            AbuseDecision.THROTTLE,
            AbuseDecision.TEMPORARILY_BLOCK,
            AbuseDecision.DEGRADED_DENY,
        }:
            await self._audit_result(
                operation=operation,
                result=result,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        return result

    async def ping(self) -> bool:
        return await self._distributed.ping()

    async def close(self) -> None:
        await self._distributed.close()
        await self._fallback.close()

    @asynccontextmanager
    async def provider_permit(
        self,
        *,
        operation: AbuseOperation,
        provider_identifier: str,
    ) -> AsyncIterator[bool]:
        owner = str(uuid4())
        safe_provider = self.fingerprint(AbuseDimension.PROVIDER_CLIENT, provider_identifier)
        if safe_provider is None:
            yield False
            return
        key = (
            f"{self._settings.redis_key_prefix}:{{portal-abuse}}:"
            f"{self._settings.abuse_policy_version}:concurrency:"
            f"{operation.value}:{safe_provider[:32]}"
        )
        request = AbuseConcurrencyRequest(
            key=key,
            operation=operation,
            limit=self._settings.abuse_provider_max_concurrency,
            lease_ms=int(self._settings.abuse_provider_concurrency_lease_seconds * 1000),
            owner_token=owner,
        )
        store = self._distributed
        lease: AbuseConcurrencyLease | None = None
        started = perf_counter()
        backend_status = AbuseBackendStatus.UP
        try:
            with self._telemetry.span(
                "abuse.provider_concurrency.acquire",
                attributes={"abuse.operation": operation.value},
            ):
                try:
                    lease = await store.acquire_concurrency(request)
                except (AbuseBackendTimeout, AbuseBackendUnavailable):
                    store = self._fallback
                    backend_status = AbuseBackendStatus.DEGRADED
                    lease = await store.acquire_concurrency(request)
            self._telemetry.record_abuse_provider_concurrency(
                operation=operation.value,
                outcome="acquired" if lease is not None else "throttled",
                backend_status=backend_status.value,
                duration_ms=(perf_counter() - started) * 1000,
            )
            yield lease is not None
        finally:
            if lease is not None:
                await store.release_concurrency(lease)

    async def _degraded_result(
        self,
        *,
        request: AbuseEvaluationRequest,
        failure_mode: AbuseFailureMode,
        timeout: bool,
    ) -> AbuseEvaluationResult:
        backend_status = AbuseBackendStatus.TIMEOUT if timeout else AbuseBackendStatus.UNAVAILABLE
        if failure_mode is AbuseFailureMode.ALWAYS_ALLOW:
            return AbuseEvaluationResult(
                decision=AbuseDecision.DEGRADED_ALLOW,
                policy_name=request.policy_name,
                policy_version=request.policy_version,
                limiting_dimension=None,
                retry_after_seconds=0,
                remaining=0,
                backend_status=backend_status,
                penalty_level=PenaltyLevel.NORMAL,
            )
        with self._telemetry.span(
            "abuse.fallback.evaluate",
            attributes={
                "abuse.operation": request.operation.value,
                "abuse.policy": request.policy_name,
            },
        ):
            fallback = await self._fallback.evaluate(request)
        if fallback.decision.permits_operation:
            decision = AbuseDecision.DEGRADED_ALLOW
        else:
            decision = AbuseDecision.DEGRADED_DENY
        return AbuseEvaluationResult(
            decision=decision,
            policy_name=fallback.policy_name,
            policy_version=fallback.policy_version,
            limiting_dimension=fallback.limiting_dimension,
            retry_after_seconds=fallback.retry_after_seconds,
            remaining=fallback.remaining,
            backend_status=backend_status,
            penalty_level=fallback.penalty_level,
        )

    async def _audit_result(
        self,
        *,
        operation: AbuseOperation,
        result: AbuseEvaluationResult,
        correlation_id: str,
        request_id: str,
    ) -> None:
        if self._audit is None:
            return
        await asyncio.to_thread(
            self._audit.record,
            operation=operation,
            result=result,
            correlation_id=correlation_id,
            request_id=request_id,
        )

    async def _audit_backend_transition(
        self,
        *,
        operation: AbuseOperation,
        result: AbuseEvaluationResult,
        correlation_id: str,
        request_id: str,
    ) -> None:
        if self._audit is None:
            return
        now = monotonic()
        async with self._backend_audit_lock:
            previous = self._last_backend_audit.get(operation, 0)
            if now - previous < self._settings.abuse_backend_audit_interval_seconds:
                return
            self._last_backend_audit[operation] = now
        await asyncio.to_thread(
            self._audit.record,
            operation=operation,
            result=result,
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code="ABUSE_BACKEND_UNAVAILABLE",
        )

    def _bucket_key(
        self,
        *,
        operation: AbuseOperation,
        policy_name: str,
        dimension: AbuseDimension,
        fingerprint: str,
    ) -> str:
        key = (
            f"{self._settings.redis_key_prefix}:{{portal-abuse}}:"
            f"{self._settings.abuse_policy_version}:{policy_name}:"
            f"{operation.value}:{dimension.value}:{fingerprint[:32]}"
        )
        if len(key) > 220:
            raise ValueError("Abuse key exceeds the bounded key length")
        return key


def bounded_retry_after(result: AbuseEvaluationResult) -> int:
    return min(3_600, max(1, ceil(result.retry_after_seconds)))
