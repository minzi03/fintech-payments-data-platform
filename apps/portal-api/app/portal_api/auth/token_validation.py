"""Strict OIDC ID-token validation for the configured issuer."""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime
from typing import Any

import jwt

from portal_api.auth.ports import OidcProviderPort, TokenValidatorPort, ValidatedIdentity
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.core.config import PortalApiSettings

MAX_CLAIMS_BYTES = 32 * 1024
MAX_GROUPS = 100
MAX_GROUP_LENGTH = 256
MAX_TOKEN_BYTES = 64 * 1024
ALLOWED_CLOCK_SKEW_SECONDS = 60


class TokenValidationError(ValueError):
    """Sanitized failure for an untrusted provider token."""


class PyJwtTokenValidator(TokenValidatorPort):
    def __init__(
        self,
        *,
        settings: PortalApiSettings,
        provider: OidcProviderPort,
        security_material: EphemeralSecurityMaterial,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._security_material = security_material

    async def validate(
        self,
        *,
        id_token: str,
        expected_nonce_hash: bytes,
    ) -> ValidatedIdentity:
        if len(id_token.encode("utf-8")) > MAX_TOKEN_BYTES:
            raise TokenValidationError("ID token exceeds the accepted bound")
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as error:
            raise TokenValidationError("ID token header is invalid") from error
        algorithm = header.get("alg")
        key_id = header.get("kid")
        token_type = header.get("typ")
        if (
            not isinstance(algorithm, str)
            or algorithm not in self._settings.oidc_allowed_algorithm_values
            or not isinstance(key_id, str)
            or not key_id
            or token_type != "JWT"
        ):
            raise TokenValidationError("ID token header is not allowed")

        jwk = await self._resolve_key(key_id=key_id, algorithm=algorithm)
        try:
            claims = jwt.decode(
                id_token,
                key=jwk.key,
                algorithms=list(self._settings.oidc_allowed_algorithm_values),
                audience=self._settings.oidc_client_id,
                issuer=self._settings.oidc_issuer,
                leeway=ALLOWED_CLOCK_SKEW_SECONDS,
                options={
                    "require": ["iss", "sub", "aud", "exp", "iat", "nonce"],
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_exp": True,
                    "verify_iat": True,
                },
            )
        except jwt.PyJWTError as error:
            raise TokenValidationError("ID token claims are invalid") from error
        if len(json.dumps(claims, separators=(",", ":")).encode("utf-8")) > MAX_CLAIMS_BYTES:
            raise TokenValidationError("ID token claims exceed the accepted bound")

        audience = claims["aud"]
        if isinstance(audience, list):
            if len(audience) > 1 and claims.get("azp") != self._settings.oidc_client_id:
                raise TokenValidationError("Authorized party is invalid")
        elif not isinstance(audience, str):
            raise TokenValidationError("Audience is invalid")
        nonce = claims["nonce"]
        subject = claims["sub"]
        issuer = claims["iss"]
        if not all(isinstance(value, str) and value for value in (nonce, subject, issuer)):
            raise TokenValidationError("Required identity claims are invalid")
        presented_nonce_hash = self._security_material.protect(
            nonce,
            purpose=ProtectedPurpose.OIDC_NONCE,
        )
        if not hmac.compare_digest(presented_nonce_hash, expected_nonce_hash):
            raise TokenValidationError("Nonce is invalid")

        raw_groups = self._claim_at_path(claims, self._settings.oidc_group_claim_path)
        groups = self._bounded_groups(raw_groups)
        display_name = claims.get("preferred_username") or claims.get("name")
        if display_name is not None and (
            not isinstance(display_name, str) or len(display_name) > 256
        ):
            raise TokenValidationError("Display attribute is invalid")
        assurance = self._assurance(claims)
        authenticated_at = datetime.fromtimestamp(
            int(claims.get("auth_time", claims["iat"])),
            tz=UTC,
        )
        return ValidatedIdentity(
            issuer=issuer,
            subject=subject,
            nonce=nonce,
            groups=groups,
            display_name=display_name,
            assurance=assurance,
            authenticated_at=authenticated_at,
            token_expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
        )

    async def _resolve_key(self, *, key_id: str, algorithm: str) -> jwt.PyJWK:
        for force_refresh in (False, True):
            jwks = await self._provider.get_jwks(force_refresh=force_refresh)
            for candidate in jwks.get("keys", []):
                if (
                    isinstance(candidate, dict)
                    and candidate.get("kid") == key_id
                    and candidate.get("alg", algorithm) == algorithm
                ):
                    try:
                        return jwt.PyJWK.from_dict(candidate, algorithm=algorithm)
                    except jwt.PyJWTError as error:
                        raise TokenValidationError("Provider signing key is invalid") from error
        raise TokenValidationError("Provider signing key is unknown")

    @staticmethod
    def _claim_at_path(claims: dict[str, Any], path: str) -> object:
        current: object = claims
        for part in path.split("."):
            if not isinstance(current, dict):
                return []
            current = current.get(part, [])
        return current

    @staticmethod
    def _bounded_groups(value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, list) or len(value) > MAX_GROUPS:
            raise TokenValidationError("Group claims are invalid")
        groups: list[str] = []
        for group in value:
            if not isinstance(group, str) or not group or len(group) > MAX_GROUP_LENGTH:
                raise TokenValidationError("Group claims are invalid")
            groups.append(group)
        return tuple(groups)

    @staticmethod
    def _assurance(claims: dict[str, Any]) -> str:
        acr = claims.get("acr")
        amr = claims.get("amr", [])
        if isinstance(acr, str) and acr.casefold() in {"aal2", "2", "urn:aal:2"}:
            return "AAL2"
        if isinstance(amr, list) and any(
            isinstance(method, str) and method.casefold() in {"mfa", "otp", "hwk"} for method in amr
        ):
            return "AAL2"
        return "AAL1"
