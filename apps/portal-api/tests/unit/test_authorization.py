"""Generated-style tests for the frozen core authorization matrix."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from portal_api.auth.authorization import AuthorizationDecision, AuthorizationService
from portal_api.auth.ports import PolicyOutcome
from portal_api.auth.session import AuthenticatedSession
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from sqlalchemy import create_engine


class RecordingAuthorizationService(AuthorizationService):
    def __init__(self, settings: PortalApiSettings) -> None:
        super().__init__(engine=create_engine("sqlite://"), settings=settings)
        self.denials: list[AuthorizationDecision] = []

    def _record_denial(
        self,
        *,
        session: AuthenticatedSession,
        decision: AuthorizationDecision,
        correlation_id: str,
        request_id: str,
    ) -> None:
        self.denials.append(decision)


def _session(
    settings: PortalApiSettings,
    *,
    roles: tuple[str, ...],
    environments: tuple[str, ...] = ("local",),
    assurance: str = "AAL1",
    policy_revision: str | None = None,
) -> AuthenticatedSession:
    now = datetime.now(UTC)
    return AuthenticatedSession(
        session_id=uuid4(),
        session_family_id=uuid4(),
        session_secret="opaque-session-secret",
        principal_id=uuid4(),
        display_name=None,
        tenant_id=settings.portal_tenant_id,
        status="ACTIVE",
        version=1,
        roles=roles,
        environment_ids=environments,
        mapping_revision=settings.identity_mapping_revision,
        policy_revision=policy_revision or settings.callback_policy_revision,
        capability_revision=settings.capability_revision,
        assurance=assurance,
        authenticated_at=now,
        idle_expires_at=now + timedelta(minutes=30),
        absolute_expires_at=now + timedelta(hours=8),
        identity_verified_until=now + timedelta(minutes=15),
        csrf_token_hash=b"x" * 32,
        csrf_generation=1,
    )


@pytest.mark.parametrize(
    "role",
    [
        "portal_viewer",
        "data_engineer_viewer",
        "platform_operator_viewer",
        "security_auditor_viewer",
        "portal_admin_viewer",
    ],
)
@pytest.mark.parametrize(
    ("action", "environment_id"),
    [
        ("portal.home.read", "local"),
        ("portal.session.read", None),
        ("portal.logout", None),
        ("portal.environment.list", None),
        ("portal.environment.select", "local"),
        ("portal.capability.read", "local"),
        ("portal.system_status.read", "local"),
    ],
)
def test_each_frozen_viewer_role_may_pass_core_action_gate(
    role: str,
    action: str,
    environment_id: str | None,
) -> None:
    settings = PortalApiSettings(environment=PortalEnvironment.TEST)
    service = RecordingAuthorizationService(settings)

    decision = service.evaluate(
        session=_session(settings, roles=(role,)),
        action=action,
        environment_id=environment_id,
        correlation_id="matrix",
        request_id="matrix-request",
    )

    assert decision.outcome is PolicyOutcome.ALLOW
    assert service.denials == []


@pytest.mark.parametrize(
    ("action", "environment_id", "expected_outcome", "expected_reason"),
    [
        ("portal.session.read", None, PolicyOutcome.ALLOW, "MINIMAL_SESSION_ALLOWED"),
        ("portal.logout", None, PolicyOutcome.ALLOW, "MINIMAL_SESSION_ALLOWED"),
        ("portal.home.read", "local", PolicyOutcome.DENY, "ROLE_ACCESS_DENIED"),
        ("portal.home.read", None, PolicyOutcome.DENY, "ENVIRONMENT_REQUIRED"),
        (
            "portal.home.read",
            "development",
            PolicyOutcome.DENY,
            "ENVIRONMENT_ACCESS_DENIED",
        ),
        ("unknown.action", None, PolicyOutcome.INDETERMINATE, "POLICY_ACTION_UNKNOWN"),
    ],
)
def test_missing_role_environment_and_unknown_action_fail_closed(
    action: str,
    environment_id: str | None,
    expected_outcome: PolicyOutcome,
    expected_reason: str,
) -> None:
    settings = PortalApiSettings(environment=PortalEnvironment.TEST)
    service = RecordingAuthorizationService(settings)
    roles = (
        ()
        if expected_reason in {"MINIMAL_SESSION_ALLOWED", "ROLE_ACCESS_DENIED"}
        else ("portal_viewer",)
    )

    decision = service.evaluate(
        session=_session(settings, roles=roles),
        action=action,
        environment_id=environment_id,
        correlation_id="matrix",
        request_id="matrix-request",
    )

    assert decision.outcome is expected_outcome
    assert decision.reason_code == expected_reason
    assert len(service.denials) == int(expected_outcome is not PolicyOutcome.ALLOW)


def test_stale_revisions_and_production_aal1_fail_closed() -> None:
    settings = PortalApiSettings(environment=PortalEnvironment.TEST)
    service = RecordingAuthorizationService(settings)
    stale = service.evaluate(
        session=_session(
            settings,
            roles=("portal_viewer",),
            policy_revision="stale-policy",
        ),
        action="portal.session.read",
        environment_id=None,
        correlation_id="matrix",
        request_id="matrix-request",
    )
    production = service.evaluate(
        session=_session(
            settings,
            roles=("portal_viewer",),
            environments=("production",),
            assurance="AAL1",
        ),
        action="portal.home.read",
        environment_id="production",
        correlation_id="matrix",
        request_id="matrix-request",
    )

    assert stale.outcome is PolicyOutcome.INDETERMINATE
    assert stale.reason_code == "AUTHORITY_REVISION_STALE"
    assert production.outcome is PolicyOutcome.DENY
    assert production.reason_code == "STEP_UP_REQUIRED"
