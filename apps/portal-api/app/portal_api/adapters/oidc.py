"""Live OIDC discovery and signing-key readiness checks."""

from __future__ import annotations

from datetime import UTC, datetime

from portal_api.adapters.models import (
    AdapterHealthResult,
    AdapterIdentity,
    DependencyStatus,
)
from portal_api.auth.ports import OidcProviderPort, ProviderExchangeFailure
from portal_api.core.config import PortalApiSettings

OIDC_ADAPTER_ID = "portal-oidc-provider"


class OidcReadinessAdapter:
    """Bypass stale caches and prove live issuer and JWKS authority."""

    identity = AdapterIdentity(
        adapter_id=OIDC_ADAPTER_ID,
        display_name="Portal OIDC provider",
        dependency_type="identity-provider",
        required=True,
        version="oidc-discovery-v1",
    )

    def __init__(
        self,
        *,
        settings: PortalApiSettings,
        provider: OidcProviderPort,
    ) -> None:
        self._settings = settings
        self._provider = provider

    async def check_health(self) -> AdapterHealthResult:
        try:
            provider = await self._provider.get_config(force_refresh=True)
        except (ProviderExchangeFailure, ValueError):
            return self._result(
                DependencyStatus.UNAVAILABLE,
                "OIDC discovery authority could not be verified.",
            )
        if provider.issuer != self._settings.oidc_issuer:
            return self._result(
                DependencyStatus.UNAVAILABLE,
                "OIDC discovery issuer does not match the configured issuer.",
            )
        try:
            jwks = await self._provider.get_jwks(force_refresh=True)
        except (ProviderExchangeFailure, ValueError):
            return self._result(
                DependencyStatus.UNAVAILABLE,
                "OIDC signing keys could not be verified.",
            )
        keys = jwks.get("keys")
        if not isinstance(keys, list) or not keys:
            return self._result(
                DependencyStatus.UNAVAILABLE,
                "OIDC signing keys are empty or invalid.",
            )
        return self._result(
            DependencyStatus.UP,
            "OIDC discovery issuer and signing keys are available.",
        )

    def _result(self, status: DependencyStatus, reason: str) -> AdapterHealthResult:
        return AdapterHealthResult(
            identity=self.identity,
            status=status,
            observed_at=datetime.now(UTC),
            reason=reason,
        )
