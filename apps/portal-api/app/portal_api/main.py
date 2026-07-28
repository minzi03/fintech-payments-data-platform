"""FastAPI application factory for the Portal BFF foundation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from portal_api.abuse.audit import AbuseAuditSink
from portal_api.abuse.client_address import (
    ClientAddressSettings,
    TrustedClientAddressResolver,
)
from portal_api.abuse.local_store import BoundedLocalAbuseStore
from portal_api.abuse.middleware import AbuseContextMiddleware
from portal_api.abuse.policy import AbusePolicyRegistry
from portal_api.abuse.redis_store import RedisAbuseStore
from portal_api.abuse.service import AbuseProtectionService
from portal_api.adapters.oidc import OIDC_ADAPTER_ID, OidcReadinessAdapter
from portal_api.adapters.postgresql import (
    POSTGRESQL_ADAPTER_ID,
    PostgreSqlReadinessAdapter,
)
from portal_api.adapters.redis import REDIS_ABUSE_ADAPTER_ID, RedisAbuseReadinessAdapter
from portal_api.adapters.registry import AdapterRegistry
from portal_api.api.health import router as health_router
from portal_api.api.v1.access import router as access_router
from portal_api.api.v1.auth import router as auth_router
from portal_api.api.v1.session import router as session_router
from portal_api.api.v1.system import router as system_router
from portal_api.auth.authorization import AuthorizationService
from portal_api.auth.callback import CallbackOrchestrator
from portal_api.auth.login_intent import LoginInitiationService
from portal_api.auth.logout_token import ProviderLogoutTokenValidator
from portal_api.auth.oidc_provider import HttpxOidcProvider
from portal_api.auth.policy import LocalDevelopmentCallbackPolicy
from portal_api.auth.ports import OidcProviderPort
from portal_api.auth.principal import ConfiguredPrincipalResolver
from portal_api.auth.protected_value import (
    AesGcmEnvelopeCipher,
    EphemeralEnvelopeCipher,
    ProtectedValueCipher,
)
from portal_api.auth.provider_session import (
    ProviderRefreshWorker,
    ProviderSessionLifecycleService,
    ProviderSessionRepository,
)
from portal_api.auth.recovery import CallbackRecovery
from portal_api.auth.refresh_token_validation import ProviderRefreshIdentityValidator
from portal_api.auth.security_material import (
    EphemeralSecurityMaterial,
    derive_security_key,
)
from portal_api.auth.session import SessionService
from portal_api.auth.session_store import CallbackSessionStore
from portal_api.auth.token_validation import PyJwtTokenValidator
from portal_api.core.config import PortalApiSettings, get_settings
from portal_api.core.errors import register_error_handlers
from portal_api.core.logging import configure_logging
from portal_api.core.middleware import RequestContextMiddleware
from portal_api.core.security import configure_security_middleware
from portal_api.db.engine import create_runtime_engine
from portal_api.db.schema_guard import validate_runtime_schema
from portal_api.health.service import HealthService
from portal_api.secret_provider import (
    PortalSecretId,
    ResolvedPortalSecrets,
    SecretProvider,
    environment_secret_provider,
    resolve_portal_secrets,
)
from portal_api.telemetry.metrics import TelemetryRecorder
from portal_api.telemetry.otel import build_telemetry

LOGGER = logging.getLogger("portal_api.lifecycle")
REQUIRED_SECURITY_ADAPTER_IDS = (POSTGRESQL_ADAPTER_ID, OIDC_ADAPTER_ID)


def _security_components(
    settings: PortalApiSettings,
    secrets: ResolvedPortalSecrets,
) -> tuple[EphemeralSecurityMaterial | None, ProtectedValueCipher | None]:
    if not settings.security_runtime_enabled:
        return None, None
    current = secrets.get(PortalSecretId.SECURITY_CURRENT_MASTER_KEY)
    if current is None:
        return EphemeralSecurityMaterial.generate(), EphemeralEnvelopeCipher()
    master_key = current.reveal_bytes()
    key_version = current.reference.version
    security_material = EphemeralSecurityMaterial.from_master_key(
        master_key,
        key_version=key_version,
    )
    previous = secrets.get(PortalSecretId.SECURITY_PREVIOUS_MASTER_KEY)
    previous_wrapping_key: bytes | None = None
    if previous is not None:
        previous_master_key = previous.reveal_bytes()
        previous_version = previous.reference.version
        transition_started_at = settings.security_key_transition_started_at
        transition_expires_at = settings.security_key_transition_expires_at
        if transition_started_at is None or transition_expires_at is None:
            raise RuntimeError("Validated security key transition is incomplete")
        security_material = security_material.with_previous(
            EphemeralSecurityMaterial.from_master_key(
                previous_master_key,
                key_version=previous_version,
            ),
            transition_started_at=transition_started_at,
            transition_expires_at=transition_expires_at,
        )
        previous_wrapping_key = derive_security_key(
            previous_master_key,
            key_version=previous_version,
            purpose="protected-value-envelope",
        )
    else:
        previous_version = None
        transition_started_at = None
        transition_expires_at = None
    return (
        security_material,
        AesGcmEnvelopeCipher(
            derive_security_key(
                master_key,
                key_version=key_version,
                purpose="protected-value-envelope",
            ),
            key_reference=key_version,
            previous_wrapping_key=previous_wrapping_key,
            previous_key_reference=previous_version,
            transition_started_at=transition_started_at,
            transition_expires_at=transition_expires_at,
        ),
    )


def create_app(
    *,
    settings: PortalApiSettings | None = None,
    adapter_registry: AdapterRegistry | None = None,
    telemetry: TelemetryRecorder | None = None,
    database_engine: Engine | None = None,
    oidc_provider: OidcProviderPort | None = None,
    secret_provider: SecretProvider | None = None,
) -> FastAPI:
    """Create an isolated Portal API without import-time infrastructure calls."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    resolved_secret_provider = secret_provider or environment_secret_provider(resolved_settings)
    resolved_secret_provider.start()
    try:
        resolved_secrets = resolve_portal_secrets(
            resolved_settings,
            resolved_secret_provider,
        )
    finally:
        resolved_secret_provider.close()
    LOGGER.info(
        "runtime secret references resolved",
        extra={
            "event": "secret_provider_resolved",
            **resolved_secrets.evidence.log_fields(),
        },
    )
    resolved_telemetry = telemetry or build_telemetry(resolved_settings)
    registry = adapter_registry or AdapterRegistry()
    owned_database_engine = (
        create_runtime_engine(resolved_settings, secrets=resolved_secrets)
        if resolved_settings.security_runtime_enabled and database_engine is None
        else None
    )
    resolved_database_engine = database_engine or owned_database_engine
    if resolved_database_engine is not None:
        resolved_telemetry.instrument_database(resolved_database_engine)
    abuse_protection_service: AbuseProtectionService | None = None
    if resolved_settings.abuse_protection_enabled:
        fingerprint_key = resolved_secrets.require_bytes(PortalSecretId.CLIENT_ADDRESS_HMAC_KEY)
        redis_url = resolved_secrets.require_text(PortalSecretId.REDIS_URL)
        abuse_resolver = TrustedClientAddressResolver(
            ClientAddressSettings(
                trusted_proxy_cidrs=resolved_settings.trusted_proxy_cidr_values,
                forwarded_header_mode=resolved_settings.forwarded_header_mode,
                max_forwarded_hops=resolved_settings.max_forwarded_hops,
                ipv4_prefix_length=resolved_settings.ipv4_prefix_length,
                ipv6_prefix_length=resolved_settings.ipv6_prefix_length,
                fingerprint_key=fingerprint_key,
            )
        )
        abuse_protection_service = AbuseProtectionService(
            settings=resolved_settings,
            resolver=abuse_resolver,
            policies=AbusePolicyRegistry.development_defaults(),
            distributed_store=RedisAbuseStore(
                url=redis_url,
                connect_timeout_seconds=resolved_settings.redis_connect_timeout_seconds,
                operation_timeout_seconds=resolved_settings.redis_operation_timeout_seconds,
                maximum_connections=resolved_settings.redis_max_connections,
            ),
            local_fallback=BoundedLocalAbuseStore(
                maximum_keys=resolved_settings.abuse_local_fallback_max_keys
            ),
            telemetry=resolved_telemetry,
            audit_sink=(
                AbuseAuditSink(resolved_database_engine)
                if resolved_database_engine is not None
                else None
            ),
        )
    security_material, protected_value_cipher = _security_components(
        resolved_settings,
        resolved_secrets,
    )
    provider: OidcProviderPort | None = None
    if resolved_settings.security_runtime_enabled:
        provider = oidc_provider or HttpxOidcProvider(
            resolved_settings,
            client_secret=resolved_secrets.require(PortalSecretId.OIDC_CLIENT_SECRET),
            telemetry=resolved_telemetry,
        )
    login_initiation_service = (
        LoginInitiationService(
            engine=resolved_database_engine,
            settings=resolved_settings,
            security_material=security_material,
            protected_value_cipher=protected_value_cipher,
            provider=provider,
        )
        if (
            resolved_settings.security_runtime_enabled
            and resolved_database_engine is not None
            and security_material is not None
            and protected_value_cipher is not None
            and provider is not None
        )
        else None
    )
    callback_orchestrator: CallbackOrchestrator | None = None
    callback_recovery: CallbackRecovery | None = None
    session_service: SessionService | None = None
    authorization_service: AuthorizationService | None = None
    provider_session_service: ProviderSessionLifecycleService | None = None
    provider_logout_token_validator: ProviderLogoutTokenValidator | None = None
    provider_refresh_worker: ProviderRefreshWorker | None = None
    if (
        resolved_settings.security_runtime_enabled
        and resolved_database_engine is not None
        and security_material is not None
        and protected_value_cipher is not None
    ):
        if provider is None:
            raise RuntimeError("OIDC provider authority is not configured")
        callback_orchestrator = CallbackOrchestrator(
            engine=resolved_database_engine,
            settings=resolved_settings,
            security_material=security_material,
            protected_value_cipher=protected_value_cipher,
            provider=provider,
            token_validator=PyJwtTokenValidator(
                settings=resolved_settings,
                provider=provider,
                security_material=security_material,
                telemetry=resolved_telemetry,
            ),
            principal_resolver=ConfiguredPrincipalResolver(
                engine=resolved_database_engine,
                settings=resolved_settings,
            ),
            policy=LocalDevelopmentCallbackPolicy(resolved_settings),
            session_store=CallbackSessionStore(
                engine=resolved_database_engine,
                settings=resolved_settings,
                security_material=security_material,
                protected_value_cipher=protected_value_cipher,
            ),
        )
        callback_recovery = CallbackRecovery(engine=resolved_database_engine)
        session_service = SessionService(
            engine=resolved_database_engine,
            settings=resolved_settings,
            security_material=security_material,
        )
        authorization_service = AuthorizationService(
            engine=resolved_database_engine,
            settings=resolved_settings,
        )
        provider_session_service = ProviderSessionLifecycleService(
            repository=ProviderSessionRepository(
                engine=resolved_database_engine,
                settings=resolved_settings,
                security_material=security_material,
                protected_value_cipher=protected_value_cipher,
            ),
            provider=provider,
            settings=resolved_settings,
            telemetry=resolved_telemetry,
            refresh_identity_validator=ProviderRefreshIdentityValidator(
                settings=resolved_settings,
                provider=provider,
                telemetry=resolved_telemetry,
            ),
            abuse_protection=abuse_protection_service,
        )
        provider_logout_token_validator = ProviderLogoutTokenValidator(
            settings=resolved_settings,
            provider=provider,
            telemetry=resolved_telemetry,
        )
        if resolved_settings.provider_refresh_enabled:
            provider_refresh_worker = ProviderRefreshWorker(
                service=provider_session_service,
                settings=resolved_settings,
            )
        if not registry.contains(POSTGRESQL_ADAPTER_ID):
            registry.register(PostgreSqlReadinessAdapter(resolved_database_engine))
        if not registry.contains(OIDC_ADAPTER_ID):
            registry.register(
                OidcReadinessAdapter(
                    settings=resolved_settings,
                    provider=provider,
                )
            )
        if abuse_protection_service is not None and not registry.contains(REDIS_ABUSE_ADAPTER_ID):
            registry.register(RedisAbuseReadinessAdapter(abuse_protection_service))

    required_adapter_ids = (
        REQUIRED_SECURITY_ADAPTER_IDS if resolved_settings.security_runtime_enabled else ()
    )
    health_service = HealthService(
        registry=registry,
        settings=resolved_settings,
        telemetry=resolved_telemetry,
        required_dependency_ids=required_adapter_ids,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        resolved_telemetry.start()
        LOGGER.info(
            "telemetry runtime configured",
            extra={
                "event": "telemetry_runtime_configured",
                "telemetry_enabled": resolved_settings.telemetry_enabled,
                "metrics_exporter": resolved_settings.telemetry_metrics_exporter.value,
                "trace_exporter": resolved_settings.telemetry_trace_exporter.value,
                "trace_sampling_ratio": resolved_settings.telemetry_trace_sampling_ratio,
            },
        )
        try:
            if resolved_settings.security_runtime_enabled:
                missing_adapters = registry.missing_required(required_adapter_ids)
                if missing_adapters:
                    LOGGER.error(
                        "required readiness adapter configuration is incomplete",
                        extra={
                            "event": "readiness_adapter_configuration_invalid",
                            "missing_dependencies": list(missing_adapters),
                        },
                    )
                    registry.validate_required(required_adapter_ids)
                LOGGER.info(
                    "required readiness adapters configured",
                    extra={
                        "event": "readiness_adapters_configured",
                        "required_dependencies": list(required_adapter_ids),
                    },
                )
                if resolved_database_engine is None:
                    raise RuntimeError("Portal security database engine is not configured")
                validate_runtime_schema(resolved_database_engine)
                if callback_recovery is None:
                    raise RuntimeError("Portal callback recovery is not configured")
                await run_in_threadpool(callback_recovery.recover_expired_claims)
                if provider_refresh_worker is not None:
                    provider_refresh_worker.start()
            LOGGER.info(
                "portal api started",
                extra={
                    "event": "application_started",
                    "version": resolved_settings.service_version,
                    "build_sha": resolved_settings.build_sha,
                },
            )
            yield
        finally:
            if provider_refresh_worker is not None:
                await provider_refresh_worker.stop()
            if abuse_protection_service is not None:
                await abuse_protection_service.close()
            resolved_telemetry.shutdown()
            if owned_database_engine is not None:
                owned_database_engine.dispose()
            LOGGER.info("portal api stopped", extra={"event": "application_stopped"})

    openapi_url = "/openapi.json" if resolved_settings.openapi_enabled else None
    docs_url = "/docs" if resolved_settings.openapi_enabled else None
    redoc_url = "/redoc" if resolved_settings.openapi_enabled else None
    app = FastAPI(
        title="Fintech Data Platform Portal API",
        summary="Foundation BFF for the Enterprise Data Platform Portal",
        description=(
            "PR-PORTAL-001 exposes safe foundation health and system metadata only. "
            "No data-platform business capabilities or mutations are enabled."
        ),
        version=resolved_settings.contract_version,
        openapi_url=openapi_url,
        docs_url=docs_url,
        redoc_url=redoc_url,
        lifespan=lifespan,
        contact={"name": "Data Platform Engineering"},
        license_info={"name": "Private repository"},
    )
    app.state.settings = resolved_settings
    app.state.adapter_registry = registry
    app.state.telemetry = resolved_telemetry
    app.state.health_service = health_service
    app.state.database_engine = resolved_database_engine
    app.state.oidc_provider = provider
    app.state.login_initiation_service = login_initiation_service
    app.state.callback_orchestrator = callback_orchestrator
    app.state.callback_recovery = callback_recovery
    app.state.session_service = session_service
    app.state.authorization_service = authorization_service
    app.state.provider_session_service = provider_session_service
    app.state.provider_logout_token_validator = provider_logout_token_validator
    app.state.provider_refresh_worker = provider_refresh_worker
    app.state.abuse_protection_service = abuse_protection_service
    app.state.security_material = security_material
    app.state.protected_value_cipher = protected_value_cipher
    app.state.secret_resolution_evidence = resolved_secrets.evidence
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(session_router)
    app.include_router(access_router)
    configure_security_middleware(app, resolved_settings)
    if abuse_protection_service is not None:
        app.add_middleware(
            AbuseContextMiddleware,
            resolver=abuse_protection_service.resolver,
        )
    app.add_middleware(RequestContextMiddleware, telemetry=resolved_telemetry)
    return app


app = create_app()
