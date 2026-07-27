"""Validated discovery, JWKS lifecycle, and confidential-client integration tests."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from portal_api.auth.oidc_provider import HttpxOidcProvider
from portal_api.auth.ports import ProviderExchangeFailure, ProviderFailureKind
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.auth.token_validation import PyJwtTokenValidator, TokenValidationError
from portal_api.core.config import PortalApiSettings, PortalEnvironment

ISSUER = "http://identity.test/realms/portal"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
AUTHORIZATION_ENDPOINT = f"{ISSUER}/protocol/openid-connect/auth"
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
CLIENT_SECRET = "integration-client-secret"


class MutableClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


def _settings() -> PortalApiSettings:
    return PortalApiSettings(
        environment=PortalEnvironment.TEST,
        oidc_issuer=ISSUER,
        oidc_discovery_url=DISCOVERY_URL,
        oidc_client_id="portal-client",
        oidc_client_secret=CLIENT_SECRET,
        oidc_redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )


def _discovery(**updates: object) -> dict[str, object]:
    metadata: dict[str, object] = {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZATION_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "jwks_uri": JWKS_URI,
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
    }
    metadata.update(updates)
    return metadata


def _key_material(key_id: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": key_id, "alg": "RS256", "use": "sig"})
    return private_key, jwk


def _token(private_key: rsa.RSAPrivateKey, *, key_id: str, nonce: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": ISSUER,
            "sub": "rotation-subject",
            "aud": "portal-client",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "iat": int(now.timestamp()),
            "auth_time": int(now.timestamp()),
            "nonce": nonce,
            "groups": ["portal_role:portal_viewer", "portal_env:local"],
        },
        private_key,
        algorithm="RS256",
        headers={"kid": key_id, "typ": "JWT"},
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_validated_discovery_is_the_only_endpoint_authority() -> None:
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, json=_discovery())

    provider = HttpxOidcProvider(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    config = await provider.get_config()

    assert requests == [DISCOVERY_URL]
    assert config.issuer == ISSUER
    assert config.authorization_endpoint == AUTHORIZATION_ENDPOINT
    assert config.token_endpoint == TOKEN_ENDPOINT
    assert config.jwks_uri == JWKS_URI


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        _discovery(issuer="http://attacker.test/realms/portal"),
        _discovery(token_endpoint="http://attacker.test/token"),
        _discovery(token_endpoint_auth_methods_supported=["none"]),
    ],
)
async def test_untrusted_discovery_metadata_fails_closed(
    metadata: dict[str, object],
) -> None:
    provider = HttpxOidcProvider(
        _settings(),
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=metadata, request=request)
        ),
    )

    with pytest.raises(ProviderExchangeFailure) as captured:
        await provider.get_config()

    assert captured.value.kind is ProviderFailureKind.AUTHORITATIVE_REJECTION


@pytest.mark.integration
@pytest.mark.asyncio
async def test_jwks_outage_policy_is_fresh_then_bounded_stale_then_fail_closed() -> None:
    clock = MutableClock()
    outage = False
    requests: list[str] = []
    _, jwk = _key_material("known-v1")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if outage:
            return httpx.Response(503, request=request)
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery(), request=request)
        if str(request.url) == JWKS_URI:
            return httpx.Response(200, json={"keys": [jwk]}, request=request)
        raise AssertionError(f"Unexpected provider request: {request.url}")

    provider = HttpxOidcProvider(
        _settings(),
        transport=httpx.MockTransport(handler),
        clock=clock,
    )
    initial = await provider.get_jwks()
    initial_request_count = len(requests)

    clock.value = 100.0
    assert await provider.get_jwks() == initial
    assert len(requests) == initial_request_count

    outage = True
    clock.value = 901.0
    assert await provider.get_jwks() == initial

    with pytest.raises(ProviderExchangeFailure) as forced:
        await provider.get_jwks(force_refresh=True)
    assert forced.value.kind is ProviderFailureKind.AMBIGUOUS

    clock.value = 3601.0
    with pytest.raises(ProviderExchangeFailure) as expired:
        await provider.get_jwks()
    assert expired.value.kind is ProviderFailureKind.AMBIGUOUS


@pytest.mark.integration
@pytest.mark.asyncio
async def test_legitimate_rotated_signing_key_succeeds_after_one_forced_refresh() -> None:
    _, old_jwk = _key_material("signing-v1")
    rotated_private_key, rotated_jwk = _key_material("signing-v2")
    jwks_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal jwks_requests
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery(), request=request)
        if str(request.url) == JWKS_URI:
            jwks_requests += 1
            selected = old_jwk if jwks_requests == 1 else rotated_jwk
            return httpx.Response(200, json={"keys": [selected]}, request=request)
        raise AssertionError(f"Unexpected provider request: {request.url}")

    settings = _settings()
    provider = HttpxOidcProvider(
        settings,
        transport=httpx.MockTransport(handler),
    )
    await provider.get_jwks()
    material = EphemeralSecurityMaterial.generate()
    nonce = "rotation-nonce"
    validator = PyJwtTokenValidator(
        settings=settings,
        provider=provider,
        security_material=material,
    )

    identity = await validator.validate(
        id_token=_token(rotated_private_key, key_id="signing-v2", nonce=nonce),
        expected_nonce_hash=material.protect(nonce, purpose=ProtectedPurpose.OIDC_NONCE),
    )

    assert identity.subject == "rotation-subject"
    assert jwks_requests == 2


@pytest.mark.integration
@pytest.mark.asyncio
async def test_unresolved_unknown_key_still_fails_closed_after_forced_refresh() -> None:
    unknown_private_key, _ = _key_material("unknown")
    _, known_jwk = _key_material("known")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery(), request=request)
        return httpx.Response(200, json={"keys": [known_jwk]}, request=request)

    settings = _settings()
    provider = HttpxOidcProvider(
        settings,
        transport=httpx.MockTransport(handler),
    )
    material = EphemeralSecurityMaterial.generate()
    validator = PyJwtTokenValidator(
        settings=settings,
        provider=provider,
        security_material=material,
    )
    nonce = "unknown-key-nonce"

    with pytest.raises(TokenValidationError, match="signing key is unknown"):
        await validator.validate(
            id_token=_token(unknown_private_key, key_id="unknown", nonce=nonce),
            expected_nonce_hash=material.protect(nonce, purpose=ProtectedPurpose.OIDC_NONCE),
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_token_exchange_uses_confidential_basic_auth_without_body_secret() -> None:
    token_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_request
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery(), request=request)
        if str(request.url) == TOKEN_ENDPOINT:
            token_request = request
            return httpx.Response(
                200,
                json={
                    "id_token": "server-only-id-token",
                    "access_token": "server-only-access-token",
                    "refresh_token": "server-only-refresh-token",
                    "token_type": "Bearer",
                    "expires_in": 300,
                },
                request=request,
            )
        raise AssertionError(f"Unexpected provider request: {request.url}")

    provider = HttpxOidcProvider(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    token_set = await provider.exchange_code(
        code="authorization-code",
        verifier="pkce-verifier",
        redirect_uri="http://portal.test/portal-api/v1/auth/callback",
    )

    assert token_request is not None
    encoded_credentials = base64.b64encode(f"portal-client:{CLIENT_SECRET}".encode("ascii")).decode(
        "ascii"
    )
    assert token_request.headers["Authorization"] == f"Basic {encoded_credentials}"
    body = parse_qs(token_request.content.decode("ascii"))
    assert "client_secret" not in body
    assert "client_id" not in body
    assert token_set.refresh_token == "server-only-refresh-token"


@pytest.mark.integration
@pytest.mark.asyncio
async def test_invalid_confidential_client_authentication_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == DISCOVERY_URL:
            return httpx.Response(200, json=_discovery(), request=request)
        if str(request.url) == TOKEN_ENDPOINT:
            return httpx.Response(
                401,
                json={"error": "invalid_client"},
                request=request,
            )
        raise AssertionError(f"Unexpected provider request: {request.url}")

    provider = HttpxOidcProvider(
        _settings(),
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderExchangeFailure) as captured:
        await provider.exchange_code(
            code="authorization-code",
            verifier="pkce-verifier",
            redirect_uri="http://portal.test/portal-api/v1/auth/callback",
        )

    assert captured.value.kind is ProviderFailureKind.AUTHORITATIVE_REJECTION
