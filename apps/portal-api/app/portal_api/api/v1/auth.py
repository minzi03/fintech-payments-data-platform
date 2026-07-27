"""Pre-authentication login-context and login-initiation transport adapter."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from portal_api.auth.cookies import set_browser_binding_cookie
from portal_api.auth.http_models import LoginContextView, LoginRequest
from portal_api.auth.login_intent import LoginInitiationError, LoginInitiationService
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
def start_login(request: Request, body: LoginRequest) -> RedirectResponse:
    try:
        redirect = _login_service(request).start_login(
            intent_token=body.intent_token,
            requested_return_path=body.return_to,
            extra_fields=frozenset((body.model_extra or {}).keys()),
            origin=request.headers.get("origin"),
            referer=request.headers.get("referer"),
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
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
