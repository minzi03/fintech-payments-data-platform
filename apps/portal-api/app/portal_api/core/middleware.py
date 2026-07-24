"""Request context, safe request logging, and telemetry middleware."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from portal_api.core.correlation import (
    CORRELATION_HEADER,
    REQUEST_HEADER,
    get_correlation_id,
    get_request_id,
    reset_request_context,
    set_request_context,
)
from portal_api.telemetry.metrics import TelemetryRecorder

LOGGER = logging.getLogger("portal_api.http")


class RequestContextMiddleware:
    """Propagate bounded correlation context through each HTTP request."""

    def __init__(self, app: Any, telemetry: TelemetryRecorder) -> None:
        self.app = app
        self.telemetry = telemetry

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        tokens = set_request_context(headers.get(CORRELATION_HEADER.lower()))
        started = perf_counter()
        status_code = 500

        async def send_with_context(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                response_headers = list(message.get("headers", []))
                response_headers.extend(
                    [
                        (CORRELATION_HEADER.lower().encode(), get_correlation_id().encode()),
                        (REQUEST_HEADER.lower().encode(), get_request_id().encode()),
                    ]
                )
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_context)
        finally:
            duration_ms = (perf_counter() - started) * 1000
            route = getattr(scope.get("route"), "path", "unmatched")
            method = scope.get("method", "UNKNOWN")
            self.telemetry.record_http(route, method, status_code, duration_ms)
            LOGGER.info(
                "request completed",
                extra={
                    "event": "http_request_completed",
                    "method": method,
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": round(duration_ms, 3),
                    "correlation_id": get_correlation_id(),
                    "request_id": get_request_id(),
                },
            )
            reset_request_context(tokens)
