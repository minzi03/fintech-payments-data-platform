"""FastAPI application factory for the Portal BFF foundation."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from portal_api.adapters.registry import AdapterRegistry
from portal_api.api.health import router as health_router
from portal_api.api.v1.system import router as system_router
from portal_api.core.config import PortalApiSettings, get_settings
from portal_api.core.errors import register_error_handlers
from portal_api.core.logging import configure_logging
from portal_api.core.middleware import RequestContextMiddleware
from portal_api.core.security import configure_security_middleware
from portal_api.health.service import HealthService
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder

LOGGER = logging.getLogger("portal_api.lifecycle")


def create_app(
    *,
    settings: PortalApiSettings | None = None,
    adapter_registry: AdapterRegistry | None = None,
    telemetry: TelemetryRecorder | None = None,
) -> FastAPI:
    """Create an isolated Portal API without import-time infrastructure calls."""
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings)
    resolved_telemetry = telemetry or NoopTelemetry()
    registry = adapter_registry or AdapterRegistry()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        LOGGER.info(
            "portal api started",
            extra={
                "event": "application_started",
                "version": resolved_settings.service_version,
                "build_sha": resolved_settings.build_sha,
            },
        )
        yield
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
    register_error_handlers(app)
    app.include_router(health_router)
    app.include_router(system_router)
    configure_security_middleware(app, resolved_settings)
    app.add_middleware(RequestContextMiddleware, telemetry=resolved_telemetry)
    return app


app = create_app()
