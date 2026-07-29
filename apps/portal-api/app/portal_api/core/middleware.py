"""Request context, safe request logging, and telemetry middleware."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import Any

from opentelemetry.trace import SpanKind, Status, StatusCode, get_current_span

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
TRACE_HEADER = "X-Trace-ID"
SPAN_HEADER = "X-Span-ID"


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
        method = scope.get("method", "UNKNOWN")
        parent_context = self.telemetry.extract(headers)
        with self.telemetry.span(
            f"HTTP {method}",
            kind=SpanKind.SERVER,
            context=parent_context,
            attributes={
                "http.request.method": method,
                "url.path": scope.get("path", ""),
                "portal.correlation_id": get_correlation_id(),
                "portal.request_id": get_request_id(),
            },
        ) as server_span:

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
                    span_context = get_current_span().get_span_context()
                    if span_context.is_valid:
                        response_headers.extend(
                            [
                                (
                                    TRACE_HEADER.lower().encode(),
                                    f"{span_context.trace_id:032x}".encode(),
                                ),
                                (
                                    SPAN_HEADER.lower().encode(),
                                    f"{span_context.span_id:016x}".encode(),
                                ),
                            ]
                        )
                    message["headers"] = response_headers
                await send(message)

            try:
                await self.app(scope, receive, send_with_context)
            except BaseException:
                if server_span is not None:
                    server_span.set_status(Status(StatusCode.ERROR))
                raise
            finally:
                duration_ms = (perf_counter() - started) * 1000
                route = getattr(scope.get("route"), "path", "unmatched")
                if server_span is not None:
                    server_span.update_name(f"{method} {route}")
                    server_span.set_attribute("http.route", route)
                    server_span.set_attribute("http.response.status_code", status_code)
                    if status_code >= 500:
                        server_span.set_status(Status(StatusCode.ERROR))
                self.telemetry.record_http(route, method, status_code, duration_ms)
                LOGGER.info(
                    "request completed",
                    extra={
                        "event": "http_request_completed",
                        "method": method,
                        "route": route,
                        "status_code": status_code,
                        "duration_ms": round(duration_ms, 3),
                        "error_category": (
                            f"http_{status_code // 100}xx" if status_code >= 400 else None
                        ),
                        "correlation_id": get_correlation_id(),
                        "request_id": get_request_id(),
                    },
                )
                reset_request_context(tokens)
