"""FastAPI application factory for the Portal BFF foundation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.engine import Engine
from starlette.concurrency import run_in_threadpool

from portal_api.adapters.registry import AdapterRegistry
from portal_api.api.health import router as health_router
from portal_api.api.v1.access import router as access_router
from portal_api.api.v1.auth import router as auth_router
from portal_api.api.v1.session import router as session_router
from portal_api.api.v1.system import router as system_router
from portal_api.auth.authorization import AuthorizationService
from portal_api.auth.callback import CallbackOrchestrator
from portal_api.auth.login_intent import LoginInitiationService
from portal_api.auth.oidc_provider import HttpxOidcProvider
from portal_api.auth.policy import LocalDevelopmentCallbackPolicy
from portal_api.auth.ports import OidcProviderPort
from portal_api.auth.principal import ConfiguredPrincipalResolver
from portal_api.auth.protected_value import (
    AesGcmEnvelopeCipher,
    EphemeralEnvelopeCipher,
    ProtectedValueCipher,
)
from portal_api.auth.recovery import CallbackRecovery
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
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder

LOGGER = logging.getLogger("portal_api.lifecycle")


def _security_components(
    settings: PortalApiSettings,
) -> tuple[EphemeralSecurityMaterial | None, ProtectedValueCipher | None]:
    if not settings.security_runtime_enabled:
        return None, None
    master_key = settings.security_master_key_bytes
    if master_key is None:
        return EphemeralSecurityMaterial.generate(), EphemeralEnvelopeCipher()
    key_version = settings.security_key_version
    security_material = EphemeralSecurityMaterial.from_master_key(
        master_key,
        key_version=key_version,
    )
    previous_master_key = settings.security_previous_master_key_bytes
    previous_wrapping_key: bytes | None = None
    if previous_master_key is not None:
        previous_version = settings.security_previous_key_version
        transition_started_at = settings.security_key_transition_started_at
        transition_expires_at = settings.security_key_transition_expires_at
        if (
            previous_version is None
            or transition_started_at is None
            or transition_expires_at is None
        ):
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
) -> FastAPI:
    """Create an isolated Portal API without import-time infrastructure calls."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    resolved_telemetry = telemetry or NoopTelemetry()
    registry = adapter_registry or AdapterRegistry()
    owned_database_engine = (
        create_runtime_engine(resolved_settings)
        if resolved_settings.security_runtime_enabled and database_engine is None
        else None
    )
    resolved_database_engine = database_engine or owned_database_engine
    security_material, protected_value_cipher = _security_components(resolved_settings)
    provider: OidcProviderPort | None = None
    if resolved_settings.security_runtime_enabled:
        provider = oidc_provider or HttpxOidcProvider(resolved_settings)
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

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if resolved_settings.security_runtime_enabled:
            if resolved_database_engine is None:
                raise RuntimeError("Portal security database engine is not configured")
            validate_runtime_schema(resolved_database_engine)
            if callback_recovery is None:
                raise RuntimeError("Portal callback recovery is not configured")
            await run_in_threadpool(callback_recovery.recover_expired_claims)
        LOGGER.info(
            "portal api started",
            extra={
                "event": "application_started",
                "version": resolved_settings.service_version,
                "build_sha": resolved_settings.build_sha,
            },
        )
        try:
            yield
        finally:
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
    app.state.health_service = HealthService(
        registry=registry,
        settings=resolved_settings,
        telemetry=resolved_telemetry,
    )
    app.state.database_engine = resolved_database_engine
    app.state.oidc_provider = provider
    app.state.login_initiation_service = login_initiation_service
    app.state.callback_orchestrator = callback_orchestrator
    app.state.callback_recovery = callback_recovery
    app.state.session_service = session_service
    app.state.authorization_service = authorization_service
    app.state.security_material = security_material
    app.state.protected_value_cipher = protected_value_cipher
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(system_router)
    app.include_router(auth_router)
    app.include_router(session_router)
    app.include_router(access_router)
    configure_security_middleware(app, resolved_settings)
    app.add_middleware(RequestContextMiddleware, telemetry=resolved_telemetry)
    return app


app = create_app()
