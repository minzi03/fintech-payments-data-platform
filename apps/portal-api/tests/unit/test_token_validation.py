"""Strict OIDC ID-token validation tests."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from portal_api.auth.ports import ProviderTokenSet
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.auth.token_validation import PyJwtTokenValidator, TokenValidationError
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.telemetry.metrics import InMemoryTelemetry


class JwksOnlyProvider:
    def __init__(self, jwk: dict[str, Any]) -> None:
        self.jwk = jwk
        self.refreshes: list[bool] = []

    async def get_config(self, *, force_refresh: bool = False) -> OidcProviderConfig:
        del force_refresh
        raise AssertionError("Discovery is not used by validation tests")

    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        raise AssertionError("Token exchange is not used by validation tests")

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        self.refreshes.append(force_refresh)
        return {"keys": [self.jwk]}


def _key_material() -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256", "use": "sig"})
    return private_key, jwk


def _settings() -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        oidc_issuer="http://identity.test/realms/portal",
        oidc_client_id="portal-client",
    )


def _claims(*, nonce: str) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "iss": "http://identity.test/realms/portal",
        "sub": "subject-123",
        "aud": "portal-client",
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "iat": int(now.timestamp()),
        "auth_time": int(now.timestamp()),
        "nonce": nonce,
        "typ": "ID",
        "preferred_username": "Portal User",
        "groups": [
            "portal_role:portal_viewer",
            "portal_env:local",
            "unknown-group",
        ],
    }


@pytest.mark.asyncio
async def test_valid_id_token_is_strictly_verified_and_bounded() -> None:
    private_key, jwk = _key_material()
    provider = JwksOnlyProvider(jwk)
    security_material = EphemeralSecurityMaterial.generate()
    nonce = "expected-nonce"
    token = jwt.encode(
        _claims(nonce=nonce),
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "JWT"},
    )
    validator = PyJwtTokenValidator(
        settings=_settings(),
        provider=provider,
        security_material=security_material,
    )

    identity = await validator.validate(
        id_token=token,
        expected_nonce_hash=security_material.protect(
            nonce,
            purpose=ProtectedPurpose.OIDC_NONCE,
        ),
    )

    assert identity.issuer == "http://identity.test/realms/portal"
    assert identity.subject == "subject-123"
    assert identity.assurance == "AAL1"
    assert identity.groups == (
        "portal_role:portal_viewer",
        "portal_env:local",
        "unknown-group",
    )
    assert provider.refreshes == [False]


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["nonce", "audience", "authorized_party"])
async def test_nonce_audience_and_authorized_party_fail_closed(mutation: str) -> None:
    private_key, jwk = _key_material()
    provider = JwksOnlyProvider(jwk)
    security_material = EphemeralSecurityMaterial.generate()
    claims = _claims(nonce="expected-nonce")
    expected_nonce = "expected-nonce"
    if mutation == "nonce":
        expected_nonce = "different-nonce"
    elif mutation == "audience":
        claims["aud"] = "different-client"
    else:
        claims["aud"] = ["portal-client", "another-client"]
        claims["azp"] = "another-client"
    token = jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "JWT"},
    )
    validator = PyJwtTokenValidator(
        settings=_settings(),
        provider=provider,
        security_material=security_material,
    )

    with pytest.raises(TokenValidationError):
        await validator.validate(
            id_token=token,
            expected_nonce_hash=security_material.protect(
                expected_nonce,
                purpose=ProtectedPurpose.OIDC_NONCE,
            ),
        )


@pytest.mark.asyncio
async def test_unknown_signing_key_refreshes_once_then_fails_closed() -> None:
    private_key, jwk = _key_material()
    jwk["kid"] = "different-key"
    provider = JwksOnlyProvider(jwk)
    material = EphemeralSecurityMaterial.generate()
    token = jwt.encode(
        _claims(nonce="nonce"),
        private_key,
        algorithm="RS256",
        headers={"kid": "unknown-key", "typ": "JWT"},
    )
    telemetry = InMemoryTelemetry()
    validator = PyJwtTokenValidator(
        settings=_settings(),
        provider=provider,
        security_material=material,
        telemetry=telemetry,
    )

    with pytest.raises(TokenValidationError):
        await validator.validate(
            id_token=token,
            expected_nonce_hash=material.protect(
                "nonce",
                purpose=ProtectedPurpose.OIDC_NONCE,
            ),
        )

    assert provider.refreshes == [False, True]
    snapshot = telemetry.snapshot()
    assert snapshot.oidc_operations["jwks:unknown_kid_refresh"] == 1
    assert snapshot.oidc_operations["validation:id_token"] == 1


@pytest.mark.asyncio
async def test_nonce_from_previous_key_is_accepted_only_during_transition() -> None:
    private_key, jwk = _key_material()
    provider = JwksOnlyProvider(jwk)
    now = datetime.now(UTC)
    previous = EphemeralSecurityMaterial.from_master_key(
        bytes(range(32)),
        key_version="local-v1",
    )
    current = EphemeralSecurityMaterial.from_master_key(
        bytes(range(32, 64)),
        key_version="local-v2",
    ).with_previous(
        previous,
        transition_started_at=now - timedelta(minutes=1),
        transition_expires_at=now + timedelta(minutes=5),
    )
    nonce = "previous-key-nonce"
    token = jwt.encode(
        _claims(nonce=nonce),
        private_key,
        algorithm="RS256",
        headers={"kid": "test-key", "typ": "JWT"},
    )
    validator = PyJwtTokenValidator(
        settings=_settings(),
        provider=provider,
        security_material=current,
    )

    identity = await validator.validate(
        id_token=token,
        expected_nonce_hash=previous.protect(
            nonce,
            purpose=ProtectedPurpose.OIDC_NONCE,
        ),
    )

    assert identity.nonce == nonce
