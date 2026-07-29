"""PostgreSQL and OIDC readiness adapter tests."""

from __future__ import annotations

import time
from typing import Any

import pytest
from portal_api.adapters.models import DependencyStatus
from portal_api.adapters.oidc import OidcReadinessAdapter
from portal_api.adapters.postgresql import PostgreSqlReadinessAdapter
from portal_api.adapters.registry import AdapterRegistry
from portal_api.auth.ports import (
    ProviderExchangeFailure,
    ProviderFailureKind,
    ProviderTokenSet,
)
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.db.schema_guard import SchemaCompatibilityError
from portal_api.health.models import ReadinessStatus
from portal_api.health.service import HealthService
from portal_api.telemetry.metrics import InMemoryTelemetry
from sqlalchemy.exc import SQLAlchemyError


class FakeScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one(self) -> object:
        return self._value


class FakeConnection:
    def __init__(self, permissions_valid: bool = True) -> None:
        self._permissions_valid = permissions_valid

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(self, statement: object) -> FakeScalarResult:
        del statement
        return FakeScalarResult(self._permissions_valid)


class FakeEngine:
    def __init__(
        self,
        *,
        permissions_valid: bool = True,
        error: Exception | None = None,
        delay: float = 0,
    ) -> None:
        self._permissions_valid = permissions_valid
        self._error = error
        self._delay = delay

    def connect(self) -> FakeConnection:
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return FakeConnection(self._permissions_valid)


class FakeOidcProvider:
    def __init__(
        self,
        settings: PortalApiSettings,
        *,
        issuer: str | None = None,
        discovery_error: bool = False,
        jwks_error: bool = False,
        empty_jwks: bool = False,
        delay: float = 0,
    ) -> None:
        self._settings = settings
        self._issuer = issuer or settings.oidc_issuer
        self._discovery_error = discovery_error
        self._jwks_error = jwks_error
        self._empty_jwks = empty_jwks
        self._delay = delay
        self.config_refreshes: list[bool] = []
        self.jwks_refreshes: list[bool] = []

    async def get_config(self, *, force_refresh: bool = False) -> OidcProviderConfig:
        import asyncio

        self.config_refreshes.append(force_refresh)
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._discovery_error:
            raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS)
        return OidcProviderConfig(
            provider_id=self._settings.oidc_provider_id,
            issuer=self._issuer,
            client_id=self._settings.oidc_client_id,
            authorization_endpoint=f"{self._issuer}/protocol/openid-connect/auth",
            token_endpoint=f"{self._issuer}/protocol/openid-connect/token",
            jwks_uri=f"{self._issuer}/protocol/openid-connect/certs",
            redirect_uri=self._settings.oidc_redirect_uri,
            scopes=self._settings.oidc_scope_values,
        )

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        self.jwks_refreshes.append(force_refresh)
        if self._jwks_error:
            raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS)
        return {"keys": [] if self._empty_jwks else [{"kid": "readiness-key"}]}

    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        del code, verifier, redirect_uri
        raise AssertionError("Readiness must not exchange authorization codes")


@pytest.mark.asyncio
async def test_postgresql_adapter_is_up_when_schema_and_permissions_are_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portal_api.adapters.postgresql.validate_runtime_schema",
        lambda engine: None,
    )
    adapter = PostgreSqlReadinessAdapter(FakeEngine())  # type: ignore[arg-type]

    result = await adapter.check_health()

    assert result.status is DependencyStatus.UP
    assert "schema" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_postgresql_adapter_rejects_schema_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_schema(engine: object) -> None:
        del engine
        raise SchemaCompatibilityError("private schema detail")

    monkeypatch.setattr(
        "portal_api.adapters.postgresql.validate_runtime_schema",
        reject_schema,
    )
    adapter = PostgreSqlReadinessAdapter(FakeEngine())  # type: ignore[arg-type]

    result = await adapter.check_health()

    assert result.status is DependencyStatus.UNAVAILABLE
    assert result.reason == "Portal schema compatibility could not be verified."
    assert "private" not in str(result)


@pytest.mark.asyncio
async def test_postgresql_adapter_rejects_unavailable_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portal_api.adapters.postgresql.validate_runtime_schema",
        lambda engine: None,
    )
    adapter = PostgreSqlReadinessAdapter(  # type: ignore[arg-type]
        FakeEngine(error=SQLAlchemyError("password=must-not-leak"))
    )

    result = await adapter.check_health()

    assert result.status is DependencyStatus.UNAVAILABLE
    assert "must-not-leak" not in str(result)


@pytest.mark.asyncio
async def test_postgresql_adapter_rejects_missing_runtime_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portal_api.adapters.postgresql.validate_runtime_schema",
        lambda engine: None,
    )
    adapter = PostgreSqlReadinessAdapter(  # type: ignore[arg-type]
        FakeEngine(permissions_valid=False)
    )

    result = await adapter.check_health()

    assert result.status is DependencyStatus.UNAVAILABLE
    assert "permissions" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_postgresql_adapter_timeout_is_bounded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "portal_api.adapters.postgresql.validate_runtime_schema",
        lambda engine: None,
    )
    adapter = PostgreSqlReadinessAdapter(FakeEngine(delay=0.1))  # type: ignore[arg-type]
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        dependency_timeout_seconds=0.01,
        health_cache_ttl_seconds=0,
    )
    service = HealthService(
        AdapterRegistry((adapter,)),
        settings,
        InMemoryTelemetry(),
    )

    dependencies = await service.dependency_summaries()

    assert dependencies[0].status is DependencyStatus.TIMEOUT
    assert service.readiness(dependencies)[0] is ReadinessStatus.NOT_READY


def _oidc_settings() -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        oidc_client_secret="readiness-secret",
    )


@pytest.mark.asyncio
async def test_oidc_adapter_forces_live_discovery_and_jwks_refresh() -> None:
    settings = _oidc_settings()
    provider = FakeOidcProvider(settings)
    adapter = OidcReadinessAdapter(settings=settings, provider=provider)

    result = await adapter.check_health()

    assert result.status is DependencyStatus.UP
    assert provider.config_refreshes == [True]
    assert provider.jwks_refreshes == [True]


@pytest.mark.asyncio
async def test_oidc_adapter_rejects_discovery_failure() -> None:
    settings = _oidc_settings()
    adapter = OidcReadinessAdapter(
        settings=settings,
        provider=FakeOidcProvider(settings, discovery_error=True),
    )

    result = await adapter.check_health()

    assert result.status is DependencyStatus.UNAVAILABLE
    assert "discovery" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_oidc_adapter_rejects_issuer_mismatch() -> None:
    settings = _oidc_settings()
    adapter = OidcReadinessAdapter(
        settings=settings,
        provider=FakeOidcProvider(settings, issuer="https://attacker.example"),
    )

    result = await adapter.check_health()

    assert result.status is DependencyStatus.UNAVAILABLE
    assert "issuer" in (result.reason or "").lower()


@pytest.mark.asyncio
async def test_oidc_adapter_rejects_jwks_failure_or_empty_key_set() -> None:
    settings = _oidc_settings()
    failed = OidcReadinessAdapter(
        settings=settings,
        provider=FakeOidcProvider(settings, jwks_error=True),
    )
    empty = OidcReadinessAdapter(
        settings=settings,
        provider=FakeOidcProvider(settings, empty_jwks=True),
    )

    failed_result = await failed.check_health()
    empty_result = await empty.check_health()

    assert failed_result.status is DependencyStatus.UNAVAILABLE
    assert empty_result.status is DependencyStatus.UNAVAILABLE


@pytest.mark.asyncio
async def test_oidc_adapter_timeout_is_bounded() -> None:
    settings = _oidc_settings().model_copy(
        update={
            "dependency_timeout_seconds": 0.01,
            "health_cache_ttl_seconds": 0,
        }
    )
    adapter = OidcReadinessAdapter(
        settings=settings,
        provider=FakeOidcProvider(settings, delay=0.1),
    )
    service = HealthService(
        AdapterRegistry((adapter,)),
        settings,
        InMemoryTelemetry(),
    )

    dependencies = await service.dependency_summaries()

    assert dependencies[0].status is DependencyStatus.TIMEOUT
    assert service.readiness(dependencies)[0] is ReadinessStatus.NOT_READY
