"""Server-derived environment, capability, and navigation projections."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from portal_api.auth.dependencies import (
    authenticated_session,
    require_action,
    require_csrf,
    session_service,
)
from portal_api.auth.session import AuthenticatedSession
from portal_api.auth.session_models import (
    CapabilityListView,
    CapabilityView,
    EnvironmentListView,
    EnvironmentSelectionRequest,
    EnvironmentSelectionView,
    EnvironmentView,
    NavigationItem,
    NavigationView,
)
from portal_api.core.correlation import get_correlation_id, get_request_id
from portal_api.core.errors import PROBLEM_RESPONSES

router = APIRouter(tags=["access"])

CORE_CAPABILITIES = (
    ("portal.home", "READ_ONLY", "AVAILABLE"),
    ("portal.session", "READ_ONLY", "AVAILABLE"),
    ("portal.environment", "READ_ONLY", "AVAILABLE"),
    ("portal.system-status", "READ_ONLY", "AVAILABLE"),
)


@router.get(
    "/v1/environments",
    response_model=EnvironmentListView,
    operation_id="listEnvironments",
    responses=PROBLEM_RESPONSES,
)
def list_environments(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> EnvironmentListView:
    require_action(
        request,
        session=session,
        action="portal.environment.list",
    )
    return EnvironmentListView(
        environments=[
            EnvironmentView(
                environment_id=environment_id,
                display_name=environment_id.replace("-", " ").title(),
            )
            for environment_id in session.environment_ids
        ],
        policy_revision=session.policy_revision,
        capability_revision=session.capability_revision,
    )


@router.post(
    "/v1/session/environment",
    response_model=EnvironmentSelectionView,
    operation_id="selectEnvironment",
    responses=PROBLEM_RESPONSES,
)
def select_environment(
    body: EnvironmentSelectionRequest,
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
) -> EnvironmentSelectionView:
    require_csrf(request, session)
    require_action(
        request,
        session=session,
        action="portal.environment.select",
        environment_id=body.environment_id,
    )
    session_service(request).record_environment_selection(
        session=session,
        environment_id=body.environment_id,
        correlation_id=get_correlation_id(),
        request_id=get_request_id(),
    )
    return EnvironmentSelectionView(
        environment_id=body.environment_id,
        policy_revision=session.policy_revision,
        capability_revision=session.capability_revision,
    )


@router.get(
    "/v1/capabilities",
    response_model=CapabilityListView,
    operation_id="listCapabilities",
    responses=PROBLEM_RESPONSES,
)
def list_capabilities(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
    environment_id: str = Query(),
) -> CapabilityListView:
    require_action(
        request,
        session=session,
        action="portal.capability.read",
        environment_id=environment_id,
    )
    return CapabilityListView(
        capabilities=[
            CapabilityView(
                capability_id=capability_id,
                environment_id=environment_id,
                mode=mode,
                state=state,
            )
            for capability_id, mode, state in CORE_CAPABILITIES
        ],
        capability_revision=session.capability_revision,
    )


@router.get(
    "/v1/navigation",
    response_model=NavigationView,
    operation_id="getNavigation",
    responses=PROBLEM_RESPONSES,
)
def get_navigation(
    request: Request,
    session: Annotated[AuthenticatedSession, Depends(authenticated_session)],
    environment_id: str = Query(),
) -> NavigationView:
    require_action(
        request,
        session=session,
        action="portal.home.read",
        environment_id=environment_id,
    )
    return NavigationView(
        environment_id=environment_id,
        items=[
            NavigationItem(label="Home", path="/", capability_id="portal.home"),
            NavigationItem(
                label="System status",
                path="/system-status",
                capability_id="portal.system-status",
            ),
        ],
        policy_revision=session.policy_revision,
        capability_revision=session.capability_revision,
    )
