"""Server-authoritative OIDC provider configuration."""

from __future__ import annotations

from dataclasses import dataclass

from portal_api.core.config import PortalApiSettings


@dataclass(frozen=True)
class OidcProviderConfig:
    provider_id: str
    issuer: str
    client_id: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    redirect_uri: str
    scopes: tuple[str, ...]

    @classmethod
    def from_settings(cls, settings: PortalApiSettings) -> OidcProviderConfig:
        return cls(
            provider_id=settings.oidc_provider_id,
            issuer=settings.oidc_issuer,
            client_id=settings.oidc_client_id,
            authorization_endpoint=settings.oidc_authorization_endpoint,
            token_endpoint=settings.oidc_token_endpoint,
            jwks_uri=settings.oidc_jwks_uri,
            redirect_uri=settings.oidc_redirect_uri,
            scopes=settings.oidc_scope_values,
        )
