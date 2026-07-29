"""Deny-by-default server authorization for the frozen Portal matrix."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.ports import PolicyOutcome
from portal_api.auth.session import AuthenticatedSession
from portal_api.core.config import PortalApiSettings
from portal_api.db.unit_of_work import local_transaction

ALL_VIEWER_ROLES = frozenset(
    {
        "portal_viewer",
        "data_engineer_viewer",
        "platform_operator_viewer",
        "security_auditor_viewer",
        "portal_admin_viewer",
    }
)
ACTION_ROLES = {
    "portal.home.read": ALL_VIEWER_ROLES,
    "portal.session.read": ALL_VIEWER_ROLES,
    "portal.logout": ALL_VIEWER_ROLES,
    "portal.environment.list": ALL_VIEWER_ROLES,
    "portal.environment.select": ALL_VIEWER_ROLES,
    "portal.capability.read": ALL_VIEWER_ROLES,
    "portal.system_status.read": ALL_VIEWER_ROLES,
}
MINIMAL_SESSION_ACTIONS = frozenset({"portal.session.read", "portal.logout"})
ENVIRONMENT_REQUIRED_ACTIONS = frozenset(
    {
        "portal.home.read",
        "portal.environment.select",
        "portal.capability.read",
        "portal.system_status.read",
    }
)


@dataclass(frozen=True)
class AuthorizationDecision:
    outcome: PolicyOutcome
    reason_code: str
    action: str
    environment_id: str | None


class AuthorizationService:
    def __init__(
        self,
        *,
        engine: Engine,
        settings: PortalApiSettings,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._audit = audit_ledger or AuditLedger()

    def evaluate(
        self,
        *,
        session: AuthenticatedSession,
        action: str,
        environment_id: str | None,
        correlation_id: str,
        request_id: str,
    ) -> AuthorizationDecision:
        outcome = PolicyOutcome.DENY
        reason = "AUTHORIZATION_DENIED"
        required_roles = ACTION_ROLES.get(action)
        if required_roles is None:
            outcome = PolicyOutcome.INDETERMINATE
            reason = "POLICY_ACTION_UNKNOWN"
        elif session.tenant_id != self._settings.portal_tenant_id:
            reason = "TENANT_ACCESS_DENIED"
        elif (
            session.policy_revision != self._settings.callback_policy_revision
            or session.capability_revision != self._settings.capability_revision
        ):
            outcome = PolicyOutcome.INDETERMINATE
            reason = "AUTHORITY_REVISION_STALE"
        elif not session.roles and action in MINIMAL_SESSION_ACTIONS:
            outcome = PolicyOutcome.ALLOW
            reason = "MINIMAL_SESSION_ALLOWED"
        elif not set(session.roles).intersection(required_roles):
            reason = "ROLE_ACCESS_DENIED"
        elif action in ENVIRONMENT_REQUIRED_ACTIONS and environment_id is None:
            reason = "ENVIRONMENT_REQUIRED"
        elif environment_id is not None and environment_id not in session.environment_ids:
            reason = "ENVIRONMENT_ACCESS_DENIED"
        elif environment_id == "production" and session.assurance != "AAL2":
            reason = "STEP_UP_REQUIRED"
        else:
            outcome = PolicyOutcome.ALLOW
            reason = "POLICY_ALLOWED"
        decision = AuthorizationDecision(
            outcome=outcome,
            reason_code=reason,
            action=action,
            environment_id=environment_id,
        )
        if outcome is not PolicyOutcome.ALLOW:
            self._record_denial(
                session=session,
                decision=decision,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        return decision

    def _record_denial(
        self,
        *,
        session: AuthenticatedSession,
        decision: AuthorizationDecision,
        correlation_id: str,
        request_id: str,
    ) -> None:
        event_type = (
            AuditEventType.AUTHORIZATION_INDETERMINATE
            if decision.outcome is PolicyOutcome.INDETERMINATE
            else AuditEventType.AUTHORIZATION_DENIED
        )
        with local_transaction(self._engine) as connection:
            self._audit.append(
                connection,
                AuditEvent(
                    event_type=event_type,
                    actor_type="PRINCIPAL",
                    principal_id=session.principal_id,
                    session_reference=str(session.session_id),
                    tenant_id=session.tenant_id,
                    environment_id=decision.environment_id,
                    action=decision.action,
                    decision=decision.outcome.value,
                    reason_code=decision.reason_code,
                    policy_revision=session.policy_revision,
                    capability_revision=session.capability_revision,
                    authentication_assurance=session.assurance,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    outcome="DENIED",
                ),
            )
