"""FastAPI enforcement dependencies for server sessions and policy."""

from __future__ import annotations

from fastapi import Request

from portal_api.auth.authorization import AuthorizationService
from portal_api.auth.cookies import session_cookie_name
from portal_api.auth.ports import PolicyOutcome
from portal_api.auth.session import (
    AuthenticatedSession,
    CsrfValidationError,
    SessionAuthenticationError,
    SessionService,
)
from portal_api.core.correlation import get_correlation_id, get_request_id
from portal_api.core.errors import ErrorCode, PortalError


def session_service(request: Request) -> SessionService:
    service = request.app.state.session_service
    if not isinstance(service, SessionService):
        raise PortalError(
            status_code=503,
            error_code=ErrorCode.SERVICE_NOT_READY,
            title="Session service unavailable",
            detail="The server session authority is not ready.",
            retryable=True,
        )
    return service


def authenticated_session(request: Request) -> AuthenticatedSession:
    settings = request.app.state.settings
    try:
        return session_service(request).authenticate(
            session_secret=request.cookies.get(session_cookie_name(settings)),
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
    except SessionAuthenticationError as error:
        raise PortalError(
            status_code=401,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Authentication required",
            detail="A current authenticated server session is required.",
            retryable=False,
        ) from error


def require_csrf(request: Request, session: AuthenticatedSession) -> None:
    try:
        session_service(request).validate_csrf(
            session=session,
            presented_token=request.headers.get("X-CSRF-Token"),
            origin=request.headers.get("Origin"),
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
    except CsrfValidationError as error:
        raise PortalError(
            status_code=403,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Request integrity check failed",
            detail="The request could not be accepted.",
            retryable=False,
        ) from error


def require_action(
    request: Request,
    *,
    session: AuthenticatedSession,
    action: str,
    environment_id: str | None = None,
) -> None:
    service = request.app.state.authorization_service
    if not isinstance(service, AuthorizationService):
        raise PortalError(
            status_code=503,
            error_code=ErrorCode.SERVICE_NOT_READY,
            title="Authorization unavailable",
            detail="The authorization authority is not ready.",
            retryable=True,
        )
    decision = service.evaluate(
        session=session,
        action=action,
        environment_id=environment_id,
        correlation_id=get_correlation_id(),
        request_id=get_request_id(),
    )
    if decision.outcome is not PolicyOutcome.ALLOW:
        raise PortalError(
            status_code=403,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Access denied",
            detail="The current session is not authorized for this action.",
            retryable=False,
        )
