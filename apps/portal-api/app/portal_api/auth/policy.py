"""Fail-closed callback session-creation policy."""

from __future__ import annotations

from portal_api.auth.ports import (
    CallbackPolicyDecision,
    CallbackPolicyPort,
    PolicyOutcome,
    ResolvedPrincipal,
)
from portal_api.core.config import PortalApiSettings, PortalEnvironment


class LocalDevelopmentCallbackPolicy(CallbackPolicyPort):
    """Explicit local/development callback policy; never grants production authority."""

    def __init__(self, settings: PortalApiSettings) -> None:
        self._settings = settings

    def evaluate(self, principal: ResolvedPrincipal) -> CallbackPolicyDecision:
        outcome = PolicyOutcome.DENY
        reason = "CALLBACK_POLICY_DENIED"
        if (
            self._settings.environment
            in {
                PortalEnvironment.LOCAL,
                PortalEnvironment.TEST,
                PortalEnvironment.DEVELOPMENT,
            }
            and principal.status == "ACTIVE"
            and principal.issuer == self._settings.oidc_issuer
            and principal.tenant_id == self._settings.portal_tenant_id
            and principal.assurance in {"AAL1", "AAL2"}
        ):
            outcome = PolicyOutcome.ALLOW
            reason = "CALLBACK_POLICY_ALLOWED_LOCAL_DEVELOPMENT"
        return CallbackPolicyDecision(
            outcome=outcome,
            reason_code=reason,
            policy_revision=self._settings.callback_policy_revision,
            capability_revision=self._settings.capability_revision,
        )
