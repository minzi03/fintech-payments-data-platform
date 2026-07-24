"""Security middleware and safe HTTP response defaults."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from portal_api.core.config import PortalApiSettings


def configure_security_middleware(app: FastAPI, settings: PortalApiSettings) -> None:
    """Install trusted-host, CORS, and response security controls."""
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_host_values))
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origin_values),
        allow_credentials=True,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID", "X-Request-ID"],
        max_age=600,
    )

    @app.middleware("http")
    async def add_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        documentation_path = request.url.path in {"/docs", "/redoc", "/openapi.json"}
        if documentation_path and settings.openapi_enabled:
            csp = (
                "default-src 'none'; "
                "script-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
                "img-src 'self' data: https://fastapi.tiangolo.com; "
                "font-src 'self' https://cdn.jsdelivr.net; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
            )
        else:
            csp = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        response.headers["Content-Security-Policy"] = csp
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cache-Control"] = "no-store"
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response
