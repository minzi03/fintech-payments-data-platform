"""Server-authoritative OIDC provider configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import SplitResult, urlsplit

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
    revocation_endpoint: str | None = None
    end_session_endpoint: str | None = None
    backchannel_logout_supported: bool = False
    frontchannel_logout_supported: bool = False

    @classmethod
    def from_discovery(
        cls,
        settings: PortalApiSettings,
        metadata: dict[str, Any],
    ) -> OidcProviderConfig:
        issuer = _required_string(metadata, "issuer")
        if issuer != settings.oidc_issuer:
            raise ValueError("OIDC discovery issuer does not match the configured issuer")

        authorization_endpoint = _validated_provider_endpoint(
            metadata,
            "authorization_endpoint",
            issuer=issuer,
        )
        token_endpoint = _validated_provider_endpoint(
            metadata,
            "token_endpoint",
            issuer=issuer,
        )
        jwks_uri = _validated_provider_endpoint(
            metadata,
            "jwks_uri",
            issuer=issuer,
        )
        revocation_endpoint = _optional_provider_endpoint(
            metadata,
            "revocation_endpoint",
            issuer=issuer,
        )
        end_session_endpoint = _optional_provider_endpoint(
            metadata,
            "end_session_endpoint",
            issuer=issuer,
        )
        token_auth_methods = metadata.get("token_endpoint_auth_methods_supported")
        if (
            not isinstance(token_auth_methods, list)
            or not all(isinstance(method, str) for method in token_auth_methods)
            or "client_secret_basic" not in token_auth_methods
        ):
            raise ValueError(
                "OIDC discovery does not advertise the governed client_secret_basic boundary"
            )

        return cls(
            provider_id=settings.oidc_provider_id,
            issuer=issuer,
            client_id=settings.oidc_client_id,
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            jwks_uri=jwks_uri,
            redirect_uri=settings.oidc_redirect_uri,
            scopes=settings.oidc_scope_values,
            revocation_endpoint=revocation_endpoint,
            end_session_endpoint=end_session_endpoint,
            backchannel_logout_supported=metadata.get("backchannel_logout_session_supported")
            is True,
            frontchannel_logout_supported=metadata.get("frontchannel_logout_session_supported")
            is True,
        )


def _required_string(metadata: dict[str, Any], field_name: str) -> str:
    value = metadata.get(field_name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"OIDC discovery field {field_name} is missing or invalid")
    return value


def _validated_provider_endpoint(
    metadata: dict[str, Any],
    field_name: str,
    *,
    issuer: str,
) -> str:
    value = _required_string(metadata, field_name)
    endpoint = urlsplit(value)
    issuer_url = urlsplit(issuer)
    if (
        "\r" in value
        or "\n" in value
        or endpoint.scheme not in {"http", "https"}
        or not endpoint.netloc
        or endpoint.username is not None
        or endpoint.password is not None
        or endpoint.fragment
    ):
        raise ValueError(f"OIDC discovery field {field_name} is not a trusted HTTP(S) endpoint")
    if _origin(endpoint) != _origin(issuer_url):
        raise ValueError(f"OIDC discovery field {field_name} is outside the configured issuer")
    return value


def _optional_provider_endpoint(
    metadata: dict[str, Any],
    field_name: str,
    *,
    issuer: str,
) -> str | None:
    value = metadata.get(field_name)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"OIDC discovery field {field_name} is invalid")
    return _validated_provider_endpoint(metadata, field_name, issuer=issuer)


def _origin(value: SplitResult) -> tuple[str, str | None, int | None]:
    hostname = value.hostname.casefold() if value.hostname else None
    return value.scheme.casefold(), hostname, value.port
