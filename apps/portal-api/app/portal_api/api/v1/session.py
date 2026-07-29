"""Authenticated server-session endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from portal_api.abuse.models import (
    AbuseDecision,
    AbuseDimension,
    AbuseOperation,
    ResolvedClientAddress,
)
from portal_api.abuse.service import AbuseProtectionService, bounded_retry_after
from portal_api.auth.cookies import set_session_cookie
from portal_api.auth.dependencies import (
    authenticated_session,
    require_action,
    require_csrf,
    session_service,
)
from portal_api.auth.session import AuthenticatedSession
from portal_api.auth.session_models import CsrfView, SessionView
from portal_api.core.correlation import get_correlation_id, get_request_id
from portal_api.core.errors import PROBLEM_RESPONSES, ErrorCode, PortalError

router = APIRouter(prefix="/v1/session", tags=["session"])


def _session_view(session: AuthenticatedSession) -> SessionView:
    return SessionView(
        session_reference=session.session_id,
        principal_reference=session.principal_id,
        tenant_id=session.tenant_id,
        status=session.status,
        roles=list(session.roles),
        environment_ids=list(session.environment_ids),
        assurance=session.assurance,
        policy_revision=session.policy_revision,
        capability_revision=session.capability_revision,
        authenticated_at=session.authenticated_at,
        idle_expires_at=session.idle_expires_at,
        absolute_expires_at=session.absolute_expires_at,
    )


@router.get(
    "",
    response_model=SessionView,
    operation_id="getSession",
    responses=PROBLEM_RESPONSES,
)
def get_session(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> SessionView:
    require_action(
        request,
        session=session,
        action="portal.session.read",
    )
    return _session_view(session)


@router.get(
    "/csrf",
    response_model=CsrfView,
    operation_id="getSessionCsrf",
    responses=PROBLEM_RESPONSES,
)
def get_session_csrf(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> CsrfView:
    require_action(
        request,
        session=session,
        action="portal.session.read",
    )
    return CsrfView(
        csrf_token=session_service(request).csrf_token(session),
        generation=session.csrf_generation,
    )


@router.post(
    "/refresh",
    response_model=SessionView,
    operation_id="refreshSession",
    responses=PROBLEM_RESPONSES,
)
async def refresh_session(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> JSONResponse:
    require_csrf(request, session)
    require_action(
        request,
        session=session,
        action="portal.session.read",
    )
    abuse = request.app.state.abuse_protection_service
    if isinstance(abuse, AbuseProtectionService):
        client = getattr(request.state, "abuse_client", None)
        result = await abuse.evaluate(
            AbuseOperation.FOREGROUND_REFRESH,
            dimensions={
                AbuseDimension.SESSION: str(session.session_family_id),
                AbuseDimension.IP_PREFIX: (
                    client.fingerprint if isinstance(client, ResolvedClientAddress) else None
                ),
                AbuseDimension.PROVIDER_ISSUER: request.app.state.settings.oidc_provider_id,
            },
            correlation_id=get_correlation_id(),
            request_id=get_request_id(),
        )
        if result.decision not in {
            AbuseDecision.ALLOW,
            AbuseDecision.DEGRADED_ALLOW,
        }:
            raise PortalError(
                status_code=429,
                error_code=ErrorCode.RATE_LIMITED,
                title="Request rate limited",
                detail="The request cannot be accepted at this time.",
                retryable=True,
                retry_after_seconds=bounded_retry_after(result),
            )
    rotated = session_service(request).rotate(
        session=session,
        correlation_id=get_correlation_id(),
        request_id=get_request_id(),
    )
    response = JSONResponse(_session_view(rotated.context).model_dump(mode="json"))
    set_session_cookie(
        response,
        settings=request.app.state.settings,
        session_secret=rotated.session_secret,
        absolute_expires_at=rotated.context.absolute_expires_at,
    )
    return response
