"""Pre-authentication login-context and login-initiation transport adapter."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse

from portal_api.auth.callback import (
    MAX_PROVIDER_ERROR_LENGTH,
    CallbackCommand,
    CallbackFailure,
    CallbackOrchestrator,
)
from portal_api.auth.cookies import (
    browser_binding_cookie,
    clear_browser_binding_cookie,
    clear_session_cookie,
    set_browser_binding_cookie,
    set_session_cookie,
)
from portal_api.auth.dependencies import (
    authenticated_session,
    require_action,
    require_csrf,
    session_service,
)
from portal_api.auth.http_models import LoginContextView, LoginRequest
from portal_api.auth.login_intent import LoginInitiationError, LoginInitiationService
from portal_api.auth.ports import ProviderExchangeFailure
from portal_api.auth.session import AuthenticatedSession
from portal_api.auth.session_models import LogoutResult
from portal_api.core.correlation import get_correlation_id, get_request_id
from portal_api.core.errors import PROBLEM_RESPONSES, ErrorCode, PortalError

router = APIRouter(prefix="/v1/auth", tags=["authentication"])


def _login_service(request: Request) -> LoginInitiationService:
    service = request.app.state.login_initiation_service
    if not isinstance(service, LoginInitiationService):
        raise PortalError(
            status_code=503,
            error_code=ErrorCode.SERVICE_NOT_READY,
            title="Authentication unavailable",
            detail="The authentication service is not ready.",
            retryable=True,
        )
    return service


def _callback_orchestrator(request: Request) -> CallbackOrchestrator:
    orchestrator = request.app.state.callback_orchestrator
    if not isinstance(orchestrator, CallbackOrchestrator):
        raise PortalError(
            status_code=503,
            error_code=ErrorCode.SERVICE_NOT_READY,
            title="Authentication unavailable",
            detail="The authentication service is not ready.",
            retryable=True,
        )
    return orchestrator


@router.get(
    "/login-context",
    response_model=LoginContextView,
    operation_id="getLoginContext",
    responses=PROBLEM_RESPONSES,
)
def login_context(
    request: Request,
    return_to: str | None = Query(default=None),
) -> LoginContextView:
    try:
        context = _login_service(request).create_context(
            requested_return_path=return_to,
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
    except LoginInitiationError as error:
        raise PortalError(
            status_code=400,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Invalid login context",
            detail="The requested login context is invalid.",
            retryable=False,
        ) from error
    return LoginContextView(
        intent_token=context.intent_token,
        selected_provider=context.selected_provider,
        return_to=context.return_to,
        expires_at=context.expires_at,
    )


@router.post(
    "/login",
    operation_id="startLogin",
    status_code=303,
    responses={**PROBLEM_RESPONSES, 303: {"description": "Redirect to the configured provider"}},
)
async def start_login(request: Request, body: LoginRequest) -> RedirectResponse:
    try:
        redirect = await _login_service(request).start_login(
            intent_token=body.intent_token,
            requested_return_path=body.return_to,
            extra_fields=frozenset((body.model_extra or {}).keys()),
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
    except ProviderExchangeFailure as error:
        raise PortalError(
            status_code=503,
            error_code=ErrorCode.SERVICE_NOT_READY,
            title="Authentication unavailable",
            detail="The configured identity provider is unavailable.",
            retryable=True,
        ) from error
    except LoginInitiationError as error:
        raise PortalError(
            status_code=403 if error.origin_failure else 400,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Login initiation rejected",
            detail="The login request could not be accepted.",
            retryable=False,
        ) from error

    response = RedirectResponse(url=redirect.location, status_code=303)
    set_browser_binding_cookie(
        response,
        settings=request.app.state.settings,
        binding_secret=redirect.browser_binding_secret,
    )
    return response


@router.get(
    "/callback",
    operation_id="completeLoginCallback",
    status_code=303,
    responses={**PROBLEM_RESPONSES, 303: {"description": "Committed session redirect"}},
)
async def complete_login_callback(request: Request) -> RedirectResponse:
    request.state.clear_browser_binding = True
    try:
        values = _bounded_callback_values(request)
        binding_name = browser_binding_cookie(request.app.state.settings).name
        session = await _callback_orchestrator(request).process(
            CallbackCommand(
                state=values["state"],
                code=values.get("code"),
                provider_error=values.get("error"),
                browser_binding=request.cookies.get(binding_name),
                provider_issuer_hint=values.get("iss"),
            ),
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
    except CallbackFailure as error:
        raise PortalError(
            status_code=error.status_code,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Authentication callback rejected",
            detail="The authentication callback could not be accepted.",
            retryable=error.retryable,
        ) from error

    response = RedirectResponse(url=session.return_path, status_code=303)
    set_session_cookie(
        response,
        settings=request.app.state.settings,
        session_secret=session.session_secret,
        absolute_expires_at=session.absolute_expires_at,
    )
    clear_browser_binding_cookie(response, settings=request.app.state.settings)
    request.state.clear_browser_binding = False
    return response


def _bounded_callback_values(request: Request) -> dict[str, str]:
    allowed = {"code", "state", "error", "error_description", "iss", "session_state"}
    grouped: dict[str, list[str]] = {}
    for key, value in request.query_params.multi_items():
        if key not in allowed:
            raise CallbackFailure("OIDC_PROVIDER_RESPONSE_INVALID")
        grouped.setdefault(key, []).append(value)
    if any(len(values) != 1 for values in grouped.values()):
        raise CallbackFailure("OIDC_PROVIDER_RESPONSE_INVALID")
    if "state" not in grouped:
        raise CallbackFailure("OIDC_STATE_INVALID")
    if "error_description" in grouped and len(grouped["error_description"][0]) > 512:
        raise CallbackFailure("OIDC_PROVIDER_RESPONSE_INVALID")
    if "error" in grouped and len(grouped["error"][0]) > MAX_PROVIDER_ERROR_LENGTH:
        raise CallbackFailure("OIDC_PROVIDER_RESPONSE_INVALID")
    return {key: values[0] for key, values in grouped.items()}


@router.post(
    "/logout",
    response_model=LogoutResult,
    operation_id="logout",
    responses=PROBLEM_RESPONSES,
)
def logout(
    request: Request,
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> LogoutResult:
    require_csrf(request, session)
    require_action(request, session=session, action="portal.logout")
    revoked = session_service(request).revoke_current(
        session=session,
        correlation_id=get_correlation_id(),
        request_id=get_request_id(),
    )
    clear_session_cookie(response, settings=request.app.state.settings)
    return LogoutResult(revoked_session_count=revoked)


@router.post(
    "/logout-all",
    response_model=LogoutResult,
    operation_id="logoutAll",
    responses=PROBLEM_RESPONSES,
)
def logout_all(
    request: Request,
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> LogoutResult:
    require_csrf(request, session)
    require_action(request, session=session, action="portal.logout")
    revoked = session_service(request).revoke_all(
        session=session,
        correlation_id=get_correlation_id(),
        request_id=get_request_id(),
    )
    clear_session_cookie(response, settings=request.app.state.settings)
    return LogoutResult(revoked_session_count=revoked)
