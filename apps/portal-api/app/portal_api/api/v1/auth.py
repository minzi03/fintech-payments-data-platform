"""Pre-authentication login-context and login-initiation transport adapter."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import parse_qs

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
)
from portal_api.auth.http_models import LoginContextView, LoginRequest
from portal_api.auth.login_intent import LoginInitiationError, LoginInitiationService
from portal_api.auth.logout_token import (
    LogoutTokenValidationError,
    ProviderLogoutTokenValidator,
)
from portal_api.auth.ports import ProviderExchangeFailure
from portal_api.auth.provider_session import (
    ProviderLogoutReplayError,
    ProviderSessionLifecycleService,
)
from portal_api.auth.session import AuthenticatedSession
from portal_api.auth.session_models import LogoutResult
from portal_api.core.correlation import get_correlation_id, get_request_id
from portal_api.core.errors import PROBLEM_RESPONSES, ErrorCode, PortalError

router = APIRouter(prefix="/v1/auth", tags=["authentication"])
MAX_BACKCHANNEL_LOGOUT_BODY_BYTES = 64 * 1024


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


def _provider_session_service(request: Request) -> ProviderSessionLifecycleService:
    service = request.app.state.provider_session_service
    if not isinstance(service, ProviderSessionLifecycleService):
        raise PortalError(
            status_code=503,
            error_code=ErrorCode.SERVICE_NOT_READY,
            title="Provider session lifecycle unavailable",
            detail="The provider session lifecycle is not ready.",
            retryable=True,
        )
    return service


def _logout_token_validator(request: Request) -> ProviderLogoutTokenValidator:
    validator = request.app.state.provider_logout_token_validator
    if not isinstance(validator, ProviderLogoutTokenValidator):
        raise PortalError(
            status_code=503,
            error_code=ErrorCode.SERVICE_NOT_READY,
            title="Provider logout validation unavailable",
            detail="The provider logout validation authority is not ready.",
            retryable=True,
        )
    return validator


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
async def logout(
    request: Request,
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> LogoutResult:
    require_csrf(request, session)
    require_action(request, session=session, action="portal.logout")
    result = await _provider_session_service(request).logout(
        session=session,
        all_for_principal=False,
        correlation_id=get_correlation_id(),
        request_id=get_request_id(),
    )
    clear_session_cookie(response, settings=request.app.state.settings)
    if result.front_channel_logout_url is not None:
        response.headers["Link"] = (
            f'<{result.front_channel_logout_url}>; rel="openid-provider-logout"'
        )
    return LogoutResult(revoked_session_count=result.revoked_session_count)


@router.post(
    "/logout-all",
    response_model=LogoutResult,
    operation_id="logoutAll",
    responses=PROBLEM_RESPONSES,
)
async def logout_all(
    request: Request,
    response: Response,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> LogoutResult:
    require_csrf(request, session)
    require_action(request, session=session, action="portal.logout")
    result = await _provider_session_service(request).logout(
        session=session,
        all_for_principal=True,
        correlation_id=get_correlation_id(),
        request_id=get_request_id(),
    )
    clear_session_cookie(response, settings=request.app.state.settings)
    if result.front_channel_logout_url is not None:
        response.headers["Link"] = (
            f'<{result.front_channel_logout_url}>; rel="openid-provider-logout"'
        )
    return LogoutResult(revoked_session_count=result.revoked_session_count)


@router.post(
    "/backchannel-logout",
    status_code=204,
    include_in_schema=False,
)
async def backchannel_logout(request: Request) -> Response:
    content_type = request.headers.get("content-type", "").partition(";")[0].strip().casefold()
    if content_type != "application/x-www-form-urlencoded":
        raise PortalError(
            status_code=400,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Provider logout rejected",
            detail="The provider logout request could not be accepted.",
            retryable=False,
        )
    body = await request.body()
    if not body or len(body) > MAX_BACKCHANNEL_LOGOUT_BODY_BYTES:
        raise PortalError(
            status_code=400,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Provider logout rejected",
            detail="The provider logout request could not be accepted.",
            retryable=False,
        )
    try:
        decoded = body.decode("ascii")
        values = parse_qs(
            decoded,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=2,
        )
        if set(values) != {"logout_token"} or len(values["logout_token"]) != 1:
            raise ValueError
        identity = await _logout_token_validator(request).validate(values["logout_token"][0])
    except (UnicodeDecodeError, ValueError, LogoutTokenValidationError) as error:
        raise PortalError(
            status_code=400,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Provider logout rejected",
            detail="The provider logout request could not be accepted.",
            retryable=False,
        ) from error
    try:
        await _provider_session_service(request).backchannel_logout(
            provider_session=identity.provider_session,
            provider_subject=identity.provider_subject,
            token_identifier=identity.token_identifier,
            issued_at=identity.issued_at,
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
    except ProviderLogoutReplayError as error:
        raise PortalError(
            status_code=400,
            error_code=ErrorCode.INVALID_REQUEST,
            title="Provider logout rejected",
            detail="The provider logout request could not be accepted.",
            retryable=False,
        ) from error
    return Response(status_code=204)
