"""Stable, sanitized Problem Details responses."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHttpException

from portal_api.core.correlation import get_correlation_id

LOGGER = logging.getLogger("portal_api.errors")


class ErrorCode(StrEnum):
    """Stable v1 machine-readable error codes."""

    PORTAL_INTERNAL_ERROR = "PORTAL_INTERNAL_ERROR"
    INVALID_REQUEST = "INVALID_REQUEST"
    RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
    DEPENDENCY_TIMEOUT = "DEPENDENCY_TIMEOUT"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    RATE_LIMITED = "RATE_LIMITED"
    SERVICE_NOT_READY = "SERVICE_NOT_READY"
    UNSUPPORTED_API_VERSION = "UNSUPPORTED_API_VERSION"


class FieldError(BaseModel):
    field: str
    message: str


class ProblemDetails(BaseModel):
    """Portal-wide error response contract."""

    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    instance: str
    error_code: str
    correlation_id: str
    timestamp: datetime
    field_errors: list[FieldError] | None = None
    retryable: bool | None = None
    retry_after_seconds: int | None = Field(default=None, ge=0)
    dependency: str | None = None
    documentation_url: str | None = None


class PortalError(Exception):
    """Expected safe application error."""

    def __init__(
        self,
        *,
        status_code: int,
        error_code: str,
        title: str,
        detail: str,
        retryable: bool = False,
        dependency: str | None = None,
    ) -> None:
        super().__init__(title)
        self.status_code = status_code
        self.error_code = error_code
        self.title = title
        self.detail = detail
        self.retryable = retryable
        self.dependency = dependency


def _problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str,
    error_code: str,
    field_errors: list[FieldError] | None = None,
    retryable: bool | None = None,
    dependency: str | None = None,
) -> JSONResponse:
    problem = ProblemDetails(
        type=f"https://docs.fintech-platform.local/problems/{error_code.lower()}",
        title=title,
        status=status,
        detail=detail,
        instance=request.url.path,
        error_code=error_code,
        correlation_id=get_correlation_id(),
        timestamp=datetime.now(UTC),
        field_errors=field_errors,
        retryable=retryable,
        dependency=dependency,
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json", exclude_none=True),
    )


def register_error_handlers(app: FastAPI) -> None:
    """Map validation, HTTP, domain, and unknown failures to Problem Details."""

    @app.exception_handler(PortalError)
    async def handle_portal_error(request: Request, error: PortalError) -> JSONResponse:
        return _problem_response(
            request,
            status=error.status_code,
            title=error.title,
            detail=error.detail,
            error_code=error.error_code,
            retryable=error.retryable,
            dependency=error.dependency,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        field_errors = [
            FieldError(
                field=".".join(str(item) for item in issue["loc"] if item not in {"body", "query"}),
                message=str(issue["msg"]),
            )
            for issue in error.errors()
        ]
        return _problem_response(
            request,
            status=422,
            title="Invalid request",
            detail="One or more request fields are invalid.",
            error_code=ErrorCode.INVALID_REQUEST,
            field_errors=field_errors,
            retryable=False,
        )

    @app.exception_handler(StarletteHttpException)
    async def handle_http_error(request: Request, error: StarletteHttpException) -> JSONResponse:
        codes = {
            404: ("Resource not found", ErrorCode.RESOURCE_NOT_FOUND),
            405: ("Method not allowed", ErrorCode.METHOD_NOT_ALLOWED),
            429: ("Rate limited", ErrorCode.RATE_LIMITED),
        }
        title, code = codes.get(
            error.status_code,
            ("Request failed", ErrorCode.INVALID_REQUEST),
        )
        return _problem_response(
            request,
            status=error.status_code,
            title=title,
            detail=title,
            error_code=code,
            retryable=error.status_code in {429, 502, 503, 504},
        )

    @app.exception_handler(Exception)
    async def handle_unknown(request: Request, error: Exception) -> JSONResponse:
        LOGGER.exception(
            "unhandled exception",
            extra={
                "event": "unhandled_exception",
                "correlation_id": get_correlation_id(),
                "exception_type": type(error).__name__,
            },
        )
        return _problem_response(
            request,
            status=500,
            title="Internal server error",
            detail="The Portal API could not complete the request.",
            error_code=ErrorCode.PORTAL_INTERNAL_ERROR,
            retryable=False,
        )


PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ProblemDetails, "description": "Resource not found"},
    405: {"model": ProblemDetails, "description": "Method not allowed"},
    422: {"model": ProblemDetails, "description": "Invalid request"},
    500: {"model": ProblemDetails, "description": "Sanitized internal error"},
}
