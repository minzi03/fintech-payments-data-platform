"""Refreshed ID-token identity continuity and signing-key rotation tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from portal_api.auth.refresh_token_validation import (
    ProviderRefreshIdentityValidator,
    RefreshIdentityValidationError,
)
from portal_api.core.config import PortalApiSettings, PortalEnvironment

ISSUER = "http://identity.test/realms/portal"


def _settings() -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        oidc_issuer=ISSUER,
        oidc_client_id="portal-client",
        oidc_client_secret="refresh-validation-secret",
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )


def _key(key_id: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": key_id, "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


class RotatingJwksProvider:
    def __init__(self, old_jwk: dict[str, Any], new_jwk: dict[str, Any]) -> None:
        self.old_jwk = old_jwk
        self.new_jwk = new_jwk
        self.requests: list[bool] = []

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        self.requests.append(force_refresh)
        return {"keys": [self.new_jwk if force_refresh else self.old_jwk]}


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    key_id: str,
    subject: str = "provider-subject",
    provider_session: str | None = "provider-session",
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "sub": subject,
        "aud": "portal-client",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    if provider_session is not None:
        claims["sid"] = provider_session
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": key_id, "typ": "JWT"},
    )


@pytest.mark.asyncio
async def test_refresh_identity_accepts_signing_key_rotation_after_forced_jwks_refresh() -> None:
    _, old_jwk = _key("old")
    new_private, new_jwk = _key("new")
    provider = RotatingJwksProvider(old_jwk, new_jwk)
    validator = ProviderRefreshIdentityValidator(
        settings=_settings(),
        provider=provider,  # type: ignore[arg-type]
    )

    await validator.validate(
        id_token=_token(new_private, key_id="new"),
        expected_subject="provider-subject",
        expected_provider_session="provider-session",
    )

    assert provider.requests == [False, True]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subject", "provider_session"),
    [
        ("other-subject", "provider-session"),
        ("provider-subject", "other-session"),
        ("provider-subject", None),
    ],
)
async def test_refresh_identity_change_fails_closed(
    subject: str,
    provider_session: str | None,
) -> None:
    private_key, jwk = _key("current")
    provider = RotatingJwksProvider(jwk, jwk)
    validator = ProviderRefreshIdentityValidator(
        settings=_settings(),
        provider=provider,  # type: ignore[arg-type]
    )

    with pytest.raises(RefreshIdentityValidationError, match="changed"):
        await validator.validate(
            id_token=_token(
                private_key,
                key_id="current",
                subject=subject,
                provider_session=provider_session,
            ),
            expected_subject="provider-subject",
            expected_provider_session="provider-session",
        )
