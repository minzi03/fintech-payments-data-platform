"""Operation-specific degradation and privacy-boundary tests."""

from __future__ import annotations

import pytest
from portal_api.abuse.client_address import (
    ClientAddressSettings,
    ForwardedHeaderMode,
    TrustedClientAddressResolver,
)
from portal_api.abuse.local_store import BoundedLocalAbuseStore
from portal_api.abuse.models import (
    AbuseConcurrencyLease,
    AbuseConcurrencyRequest,
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationRequest,
    AbuseEvaluationResult,
    AbuseOperation,
)
from portal_api.abuse.policy import AbusePolicyRegistry
from portal_api.abuse.ports import AbuseBackendUnavailable
from portal_api.abuse.service import AbuseProtectionService
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.telemetry.metrics import InMemoryTelemetry

SECRET = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


class FailingStore:
    def __init__(self) -> None:
        self.requests: list[AbuseEvaluationRequest] = []

    async def evaluate(self, request: AbuseEvaluationRequest) -> AbuseEvaluationResult:
        self.requests.append(request)
        raise AbuseBackendUnavailable("not available")

    async def acquire_concurrency(
        self,
        request: AbuseConcurrencyRequest,
    ) -> AbuseConcurrencyLease | None:
        del request
        raise AbuseBackendUnavailable("not available")

    async def release_concurrency(self, lease: AbuseConcurrencyLease) -> None:
        del lease

    async def ping(self) -> bool:
        raise AbuseBackendUnavailable("not available")

    async def close(self) -> None:
        return None


def _service(
    distributed: FailingStore,
) -> tuple[AbuseProtectionService, InMemoryTelemetry]:
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        client_address_hmac_secret=SECRET,
    )
    resolver = TrustedClientAddressResolver(
        ClientAddressSettings(
            trusted_proxy_cidrs=(),
            forwarded_header_mode=ForwardedHeaderMode.DIRECT,
            max_forwarded_hops=5,
            ipv4_prefix_length=24,
            ipv6_prefix_length=64,
            fingerprint_key=bytes(range(32)),
        )
    )
    telemetry = InMemoryTelemetry()
    return (
        AbuseProtectionService(
            settings=settings,
            resolver=resolver,
            policies=AbusePolicyRegistry.development_defaults(),
            distributed_store=distributed,
            local_fallback=BoundedLocalAbuseStore(maximum_keys=100),
            telemetry=telemetry,
        ),
        telemetry,
    )


@pytest.mark.asyncio
async def test_backend_outage_activates_bounded_local_fallback_without_raw_keys() -> None:
    distributed = FailingStore()
    service, telemetry = _service(distributed)

    result = await service.evaluate(
        AbuseOperation.LOGIN_INITIATION,
        dimensions={AbuseDimension.IP_PREFIX: "192.0.2.0/24"},
        correlation_id="correlation",
        request_id="request",
    )

    assert result.decision is AbuseDecision.DEGRADED_ALLOW
    serialized_keys = "|".join(
        bucket.key for request in distributed.requests for bucket in request.buckets
    )
    assert "192.0.2" not in serialized_keys
    assert "@" not in serialized_keys
    assert telemetry.snapshot().abuse_operations["fallback:login_initiation"] == 1


@pytest.mark.asyncio
async def test_invalid_auth_traffic_fails_closed_when_local_budget_is_exhausted() -> None:
    service, _ = _service(FailingStore())
    decisions = [
        (
            await service.evaluate(
                AbuseOperation.INVALID_AUTH_TRAFFIC,
                dimensions={AbuseDimension.IP_PREFIX: "privacy-safe-client"},
                correlation_id=f"correlation-{index}",
                request_id=f"request-{index}",
            )
        ).decision
        for index in range(5)
    ]

    assert decisions[:4] == [AbuseDecision.DEGRADED_ALLOW] * 4
    assert decisions[4] is AbuseDecision.DEGRADED_DENY


@pytest.mark.asyncio
async def test_logout_is_degraded_allow_without_consulting_local_quota() -> None:
    service, _ = _service(FailingStore())

    result = await service.evaluate(
        AbuseOperation.LOGOUT,
        dimensions={
            AbuseDimension.SESSION: "session-reference",
            AbuseDimension.IP_PREFIX: "client-reference",
        },
        correlation_id="correlation",
        request_id="request",
    )

    assert result.decision is AbuseDecision.DEGRADED_ALLOW


@pytest.mark.asyncio
async def test_provider_concurrency_uses_process_local_bounded_fallback() -> None:
    service, telemetry = _service(FailingStore())

    async with service.provider_permit(
        operation=AbuseOperation.BACKGROUND_REFRESH,
        provider_identifier="configured-provider",
    ) as allowed:
        assert allowed is True

    snapshot = telemetry.snapshot()
    assert snapshot.abuse_operations["provider:background_refresh:acquired"] == 1
