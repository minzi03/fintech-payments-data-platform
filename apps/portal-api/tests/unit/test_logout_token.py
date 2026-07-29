"""OIDC back-channel logout-token validation tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from portal_api.auth.logout_token import (
    BACKCHANNEL_LOGOUT_EVENT,
    LogoutTokenValidationError,
    ProviderLogoutTokenValidator,
)
from portal_api.core.config import PortalApiSettings, PortalEnvironment

ISSUER = "http://identity.test/realms/portal"


def _settings() -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        oidc_issuer=ISSUER,
        oidc_client_id="portal-client",
        oidc_client_secret="logout-test-secret",
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )


def _key() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    public_jwk.update({"kid": "logout-v1", "alg": "RS256", "use": "sig"})
    return private_key, public_jwk


class LogoutProvider:
    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk
        self.requests: list[bool] = []

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        self.requests.append(force_refresh)
        return {"keys": [self.jwk]}


def _token(
    private_key: rsa.RSAPrivateKey,
    *,
    overrides: dict[str, object] | None = None,
) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": "portal-client",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "jti": "logout-event-1",
        "sid": "provider-session-1",
        "sub": "provider-subject-1",
        "events": {BACKCHANNEL_LOGOUT_EVENT: {}},
    }
    claims.update(overrides or {})
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "logout-v1", "typ": "logout+jwt"},
    )


@pytest.mark.asyncio
async def test_valid_backchannel_logout_token_returns_bounded_provider_identity() -> None:
    private_key, jwk = _key()
    validator = ProviderLogoutTokenValidator(
        settings=_settings(),
        provider=LogoutProvider(jwk),  # type: ignore[arg-type]
    )

    identity = await validator.validate(_token(private_key))

    assert identity.provider_session == "provider-session-1"
    assert identity.provider_subject == "provider-subject-1"
    assert identity.token_identifier == "logout-event-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "overrides",
    [
        {"events": {}},
        {"nonce": "forbidden"},
        {"sid": None, "sub": None},
        {"aud": "other-client"},
        {"iat": int((datetime.now(UTC) - timedelta(days=2)).timestamp())},
    ],
)
async def test_invalid_logout_authority_fails_closed(
    overrides: dict[str, object],
) -> None:
    private_key, jwk = _key()
    validator = ProviderLogoutTokenValidator(
        settings=_settings(),
        provider=LogoutProvider(jwk),  # type: ignore[arg-type]
    )

    with pytest.raises(LogoutTokenValidationError):
        await validator.validate(_token(private_key, overrides=overrides))
