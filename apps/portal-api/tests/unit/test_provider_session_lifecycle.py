"""Deterministic provider-session state, refresh, logout, and load tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from portal_api.abuse.models import (
    AbuseBackendStatus,
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationResult,
    AbuseOperation,
    PenaltyLevel,
)
from portal_api.auth.ports import (
    ProviderExchangeFailure,
    ProviderFailureKind,
    ProviderOperationResult,
    ProviderOperationStatus,
    ProviderRefreshTokenSet,
    ProviderTokenKind,
)
from portal_api.auth.provider_session import (
    CleanupEvidence,
    LogoutPlan,
    LogoutTokenTarget,
    ProviderSessionLifecycleService,
    ProviderSessionState,
    ProviderTokens,
    RefreshClaim,
    require_provider_transition,
)
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.telemetry.metrics import InMemoryTelemetry


def _settings(**updates: object) -> PortalApiSettings:
    values: dict[str, object] = {
        "environment": PortalEnvironment.TEST,
        "oidc_issuer": "http://identity.test/realms/portal",
        "oidc_client_id": "portal-client",
        "oidc_client_secret": "provider-session-test",
        "oidc_redirect_uri": "http://portal.test/portal-api/v1/auth/callback",
        "provider_refresh_enabled": True,
    }
    values.update(updates)
    return PortalApiSettings(**values)


def _claim(*, failures: int = 0) -> RefreshClaim:
    return RefreshClaim(
        envelope_id=uuid4(),
        session_family_id=uuid4(),
        worker_id="worker-1",
        token_generation=1,
        refresh_failures=failures,
        provider_subject="provider-subject",
        provider_session="provider-session",
        previous_refresh_token_fingerprint=None,
        tokens=ProviderTokens(
            id_token="protected-id",
            access_token="protected-access",
            refresh_token="protected-refresh",
        ),
    )


class FakeRepository:
    def __init__(self, claims: tuple[RefreshClaim, ...] = ()) -> None:
        self.claims = claims
        self.completed: list[RefreshClaim] = []
        self.failures: list[tuple[RefreshClaim, ProviderExchangeFailure]] = []
        self.finished_logout: tuple[LogoutPlan, tuple[CleanupEvidence, ...]] | None = None
        self.deferred: list[tuple[RefreshClaim, str]] = []
        self.backchannel_calls = 0

    def claim_due(self, **kwargs: object) -> tuple[RefreshClaim, ...]:
        del kwargs
        result = self.claims
        self.claims = ()
        return result

    def complete_refresh(
        self,
        *,
        claim: RefreshClaim,
        refreshed: ProviderRefreshTokenSet,
        **kwargs: object,
    ) -> str:
        del refreshed, kwargs
        self.completed.append(claim)
        return "rotated"

    def fail_refresh(
        self,
        *,
        claim: RefreshClaim,
        failure: ProviderExchangeFailure,
        **kwargs: object,
    ) -> str:
        del kwargs
        self.failures.append((claim, failure))
        return "revoked" if failure.reason_code == "INVALID_GRANT" else "retry_scheduled"

    def defer_refresh(
        self,
        *,
        claim: RefreshClaim,
        reason_code: str,
        **kwargs: object,
    ) -> str:
        del kwargs
        self.deferred.append((claim, reason_code))
        return "deferred"

    def begin_logout(self, **kwargs: object) -> LogoutPlan:
        del kwargs
        target = LogoutTokenTarget(
            envelope_id=uuid4(),
            session_family_id=uuid4(),
            tokens=ProviderTokens("id", "access", "refresh"),
        )
        return LogoutPlan(1, (target,), "correlation", "request")

    def finish_logout(
        self,
        *,
        plan: LogoutPlan,
        evidence: tuple[CleanupEvidence, ...],
        **kwargs: object,
    ) -> None:
        del kwargs
        self.finished_logout = (plan, evidence)

    def record_logout_cleanup(
        self,
        *,
        plan: LogoutPlan,
        evidence: tuple[CleanupEvidence, ...],
    ) -> None:
        self.finished_logout = (plan, evidence)

    def revoke_from_backchannel(self, **kwargs: object) -> int:
        del kwargs
        self.backchannel_calls += 1
        return 2


class FakeProvider:
    def __init__(
        self,
        *,
        refresh_failure: ProviderExchangeFailure | None = None,
        refresh_delay: float = 0,
        cleanup_failure: ProviderExchangeFailure | None = None,
    ) -> None:
        self.refresh_failure = refresh_failure
        self.refresh_delay = refresh_delay
        self.cleanup_failure = cleanup_failure
        self.refresh_calls = 0
        self.cleanup_calls: list[str] = []

    async def refresh_tokens(self, *, refresh_token: str) -> ProviderRefreshTokenSet:
        assert refresh_token == "protected-refresh"
        self.refresh_calls += 1
        if self.refresh_delay:
            await asyncio.sleep(self.refresh_delay)
        if self.refresh_failure is not None:
            raise self.refresh_failure
        return ProviderRefreshTokenSet(
            access_token="new-access",
            refresh_token="new-refresh",
            id_token=None,
            token_type="Bearer",
            expires_in=300,
            refresh_expires_in=1800,
        )

    async def revoke_token(
        self,
        *,
        token: str,
        token_kind: ProviderTokenKind,
    ) -> ProviderOperationResult:
        assert token
        self.cleanup_calls.append(token_kind.value)
        if self.cleanup_failure is not None:
            raise self.cleanup_failure
        return ProviderOperationResult(ProviderOperationStatus.SUCCEEDED)

    async def logout_provider_session(
        self,
        *,
        refresh_token: str | None,
    ) -> ProviderOperationResult:
        assert refresh_token == "refresh"
        self.cleanup_calls.append("provider_logout")
        if self.cleanup_failure is not None:
            raise self.cleanup_failure
        return ProviderOperationResult(ProviderOperationStatus.SUCCEEDED)

    async def front_channel_logout_url(self) -> str | None:
        return "http://identity.test/logout"


class FakeAbuseProtection:
    def __init__(self, *, allow: bool, provider_permit: bool = True) -> None:
        self.allow = allow
        self.provider_permitted = provider_permit
        self.evaluations: list[tuple[AbuseOperation, dict[AbuseDimension, str | None]]] = []

    async def evaluate(
        self,
        operation: AbuseOperation,
        *,
        dimensions: dict[AbuseDimension, str | None],
        **kwargs: object,
    ) -> AbuseEvaluationResult:
        del kwargs
        self.evaluations.append((operation, dimensions))
        return AbuseEvaluationResult(
            decision=AbuseDecision.ALLOW if self.allow else AbuseDecision.THROTTLE,
            policy_name="test",
            policy_version="test-v1",
            limiting_dimension=None if self.allow else AbuseDimension.SESSION,
            retry_after_seconds=0 if self.allow else 30,
            remaining=1 if self.allow else 0,
            backend_status=AbuseBackendStatus.UP,
            penalty_level=PenaltyLevel.NORMAL,
        )

    async def _provider_permit_context(self):
        yield self.provider_permitted

    def provider_permit(self, **kwargs: object):
        from contextlib import asynccontextmanager

        del kwargs
        return asynccontextmanager(self._provider_permit_context)()


def test_state_machine_contains_all_required_states_and_rejects_illegal_transitions() -> None:
    assert {state.value for state in ProviderSessionState} == {
        "ACTIVE",
        "REFRESH_PENDING",
        "REFRESHING",
        "REFRESH_REQUIRED",
        "REFRESH_FAILED",
        "EXPIRED",
        "LOGGED_OUT",
        "REVOKED",
        "DISPOSED",
    }
    require_provider_transition(
        ProviderSessionState.ACTIVE,
        ProviderSessionState.REFRESH_PENDING,
    )
    require_provider_transition(
        ProviderSessionState.REFRESH_PENDING,
        ProviderSessionState.REFRESHING,
    )
    require_provider_transition(
        ProviderSessionState.REFRESHING,
        ProviderSessionState.ACTIVE,
    )
    with pytest.raises(ValueError, match="invalid"):
        require_provider_transition(
            ProviderSessionState.DISPOSED,
            ProviderSessionState.ACTIVE,
        )


@pytest.mark.asyncio
async def test_refresh_success_rotates_once_and_emits_bounded_telemetry() -> None:
    claim = _claim()
    repository = FakeRepository((claim,))
    provider = FakeProvider()
    telemetry = InMemoryTelemetry()
    service = ProviderSessionLifecycleService(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        settings=_settings(),
        telemetry=telemetry,
    )

    processed = await service.refresh_due(worker_id="worker-1")
    duplicate_scan = await service.refresh_due(worker_id="worker-1")

    assert processed == 1
    assert duplicate_scan == 0
    assert provider.refresh_calls == 1
    assert repository.completed == [claim]
    assert not repository.failures
    snapshot = telemetry.snapshot()
    assert snapshot.provider_session_operations["refresh:rotated"] == 1
    assert snapshot.provider_session_operations["transition:REFRESHING:ACTIVE"] == 1


@pytest.mark.asyncio
async def test_invalid_grant_and_timeout_are_classified_without_token_exposure() -> None:
    invalid_claim = _claim()
    invalid_repository = FakeRepository((invalid_claim,))
    invalid_service = ProviderSessionLifecycleService(
        repository=invalid_repository,  # type: ignore[arg-type]
        provider=FakeProvider(
            refresh_failure=ProviderExchangeFailure(
                ProviderFailureKind.AUTHORITATIVE_REJECTION,
                reason_code="INVALID_GRANT",
            )
        ),  # type: ignore[arg-type]
        settings=_settings(),
    )
    await invalid_service.refresh_due(worker_id="worker-1")
    assert invalid_repository.failures[0][1].reason_code == "INVALID_GRANT"

    timeout_claim = _claim()
    timeout_repository = FakeRepository((timeout_claim,))
    timeout_service = ProviderSessionLifecycleService(
        repository=timeout_repository,  # type: ignore[arg-type]
        provider=FakeProvider(refresh_delay=0.05),  # type: ignore[arg-type]
        settings=_settings(oidc_http_timeout_seconds=0.01),
    )
    await timeout_service.refresh_due(worker_id="worker-1")
    assert timeout_repository.failures[0][1].reason_code == "PROVIDER_TIMEOUT"
    assert "protected-refresh" not in repr(timeout_claim.tokens)


@pytest.mark.asyncio
async def test_background_refresh_throttle_has_no_ip_and_does_not_charge_retry_budget() -> None:
    claim = _claim(failures=2)
    repository = FakeRepository((claim,))
    provider = FakeProvider()
    abuse = FakeAbuseProtection(allow=False)
    service = ProviderSessionLifecycleService(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        settings=_settings(),
        abuse_protection=abuse,  # type: ignore[arg-type]
    )

    assert await service.refresh_due(worker_id="worker-1") == 1

    assert provider.refresh_calls == 0
    assert repository.deferred == [(claim, "ABUSE_THROTTLED")]
    assert not repository.failures
    operation, dimensions = abuse.evaluations[0]
    assert operation is AbuseOperation.BACKGROUND_REFRESH
    assert AbuseDimension.IP_PREFIX not in dimensions
    assert dimensions[AbuseDimension.SESSION] == str(claim.session_family_id)


@pytest.mark.asyncio
async def test_logout_orders_provider_cleanup_and_preserves_local_completion() -> None:
    repository = FakeRepository()
    provider = FakeProvider()
    telemetry = InMemoryTelemetry()
    service = ProviderSessionLifecycleService(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        settings=_settings(),
        telemetry=telemetry,
    )
    session = object()

    result = await service.logout(
        session=session,  # type: ignore[arg-type]
        all_for_principal=False,
        correlation_id="correlation",
        request_id="request",
    )

    assert result.revoked_session_count == 1
    assert result.front_channel_logout_url == "http://identity.test/logout"
    assert result.provider_failures == 0
    assert provider.cleanup_calls == [
        "provider_logout",
        "access_token",
        "refresh_token",
    ]
    assert repository.finished_logout is not None
    assert [item.outcome for item in repository.finished_logout[1]] == [
        "SUCCEEDED",
        "SUCCEEDED",
        "SUCCEEDED",
    ]


@pytest.mark.asyncio
async def test_logout_commits_locally_when_provider_cleanup_is_throttled() -> None:
    repository = FakeRepository()
    provider = FakeProvider()
    service = ProviderSessionLifecycleService(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        settings=_settings(),
    )

    result = await service.logout(
        session=object(),  # type: ignore[arg-type]
        all_for_principal=False,
        correlation_id="correlation",
        request_id="request",
        provider_cleanup_allowed=False,
    )

    assert result.revoked_session_count == 1
    assert result.front_channel_logout_url is None
    assert provider.cleanup_calls == []
    assert repository.finished_logout is not None
    assert repository.finished_logout[1][0].outcome == "THROTTLED"
    assert repository.finished_logout[1][0].reason_code == "ABUSE_THROTTLED"


@pytest.mark.asyncio
async def test_provider_outage_cannot_undo_committed_local_logout() -> None:
    repository = FakeRepository()
    provider = FakeProvider(
        cleanup_failure=ProviderExchangeFailure(
            ProviderFailureKind.AMBIGUOUS,
            reason_code="PROVIDER_UNAVAILABLE",
        )
    )
    service = ProviderSessionLifecycleService(
        repository=repository,  # type: ignore[arg-type]
        provider=provider,  # type: ignore[arg-type]
        settings=_settings(),
    )

    result = await service.logout(
        session=object(),  # type: ignore[arg-type]
        all_for_principal=False,
        correlation_id="correlation",
        request_id="request",
    )

    assert result.revoked_session_count == 1
    assert result.provider_failures == 3
    assert repository.finished_logout is not None
    assert [item.reason_code for item in repository.finished_logout[1]] == [
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_UNAVAILABLE",
    ]


def test_ten_thousand_refresh_state_cycles_are_deterministic() -> None:
    started = datetime.now(UTC)
    for _ in range(10_000):
        require_provider_transition(
            ProviderSessionState.ACTIVE,
            ProviderSessionState.REFRESH_PENDING,
        )
        require_provider_transition(
            ProviderSessionState.REFRESH_PENDING,
            ProviderSessionState.REFRESHING,
        )
        require_provider_transition(
            ProviderSessionState.REFRESHING,
            ProviderSessionState.ACTIVE,
        )
    assert (datetime.now(UTC) - started).total_seconds() < 2
