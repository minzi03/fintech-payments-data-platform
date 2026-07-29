"""Fail-closed OIDC back-channel logout-token validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt

from portal_api.auth.ports import OidcProviderPort
from portal_api.core.config import PortalApiSettings
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder

BACKCHANNEL_LOGOUT_EVENT = "http://schemas.openid.net/event/backchannel-logout"
MAX_LOGOUT_TOKEN_BYTES = 64 * 1024
MAX_LOGOUT_CLAIMS_BYTES = 32 * 1024
ALLOWED_CLOCK_SKEW_SECONDS = 60


class LogoutTokenValidationError(ValueError):
    """Sanitized provider logout-token rejection."""


@dataclass(frozen=True)
class ProviderLogoutIdentity:
    provider_session: str | None
    provider_subject: str | None
    token_identifier: str
    issued_at: datetime


class ProviderLogoutTokenValidator:
    def __init__(
        self,
        *,
        settings: PortalApiSettings,
        provider: OidcProviderPort,
        telemetry: TelemetryRecorder | None = None,
    ) -> None:
        self._settings = settings
        self._provider = provider
        self._telemetry = telemetry or NoopTelemetry()

    async def validate(self, logout_token: str) -> ProviderLogoutIdentity:
        try:
            return await self._validate(logout_token)
        except LogoutTokenValidationError:
            self._telemetry.record_oidc_validation_failure("logout_token")
            raise

    async def _validate(self, logout_token: str) -> ProviderLogoutIdentity:
        if not logout_token or len(logout_token.encode("utf-8")) > MAX_LOGOUT_TOKEN_BYTES:
            raise LogoutTokenValidationError("Logout token is invalid")
        try:
            header = jwt.get_unverified_header(logout_token)
        except jwt.PyJWTError as error:
            raise LogoutTokenValidationError("Logout token header is invalid") from error
        algorithm = header.get("alg")
        key_id = header.get("kid")
        token_type = header.get("typ")
        if (
            not isinstance(algorithm, str)
            or algorithm not in self._settings.oidc_allowed_algorithm_values
            or not isinstance(key_id, str)
            or not key_id
            or token_type not in {None, "JWT", "logout+jwt"}
        ):
            raise LogoutTokenValidationError("Logout token header is not allowed")
        jwk = await self._resolve_key(key_id=key_id, algorithm=algorithm)
        try:
            claims = jwt.decode(
                logout_token,
                key=jwk.key,
                algorithms=list(self._settings.oidc_allowed_algorithm_values),
                audience=self._settings.oidc_client_id,
                issuer=self._settings.oidc_issuer,
                leeway=ALLOWED_CLOCK_SKEW_SECONDS,
                options={
                    "require": ["iss", "aud", "iat", "jti", "events"],
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_iat": True,
                    "verify_exp": True,
                },
            )
        except jwt.PyJWTError as error:
            raise LogoutTokenValidationError("Logout token claims are invalid") from error
        if len(json.dumps(claims, separators=(",", ":")).encode("utf-8")) > MAX_LOGOUT_CLAIMS_BYTES:
            raise LogoutTokenValidationError("Logout token claims exceed the accepted bound")
        events = claims.get("events")
        if (
            not isinstance(events, dict)
            or BACKCHANNEL_LOGOUT_EVENT not in events
            or not isinstance(events[BACKCHANNEL_LOGOUT_EVENT], dict)
            or "nonce" in claims
        ):
            raise LogoutTokenValidationError("Logout token event is invalid")
        issued_at = claims.get("iat")
        evaluated_at = datetime.now(UTC)
        issued_datetime = (
            datetime.fromtimestamp(issued_at, tz=UTC)
            if isinstance(issued_at, int) and not isinstance(issued_at, bool)
            else None
        )
        if (
            not isinstance(issued_at, int)
            or isinstance(issued_at, bool)
            or issued_datetime is None
            or issued_datetime > evaluated_at
            or evaluated_at - issued_datetime
            > timedelta(seconds=self._settings.provider_logout_replay_ttl_seconds)
        ):
            raise LogoutTokenValidationError("Logout token issue time is invalid")
        token_identifier = self._bounded_reference(claims.get("jti"), name="jti")
        if token_identifier is None or not token_identifier.isascii():
            raise LogoutTokenValidationError("Logout token identifier is invalid")
        provider_session = self._bounded_reference(claims.get("sid"), name="sid")
        provider_subject = self._bounded_reference(claims.get("sub"), name="sub")
        if provider_session is None and provider_subject is None:
            raise LogoutTokenValidationError("Logout token has no session authority")
        return ProviderLogoutIdentity(
            provider_session=provider_session,
            provider_subject=provider_subject,
            token_identifier=token_identifier,
            issued_at=issued_datetime,
        )

    async def _resolve_key(self, *, key_id: str, algorithm: str) -> jwt.PyJWK:
        for force_refresh in (False, True):
            if force_refresh:
                self._telemetry.record_oidc_unknown_kid_refresh()
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
                        raise LogoutTokenValidationError(
                            "Provider logout signing key is invalid"
                        ) from error
        raise LogoutTokenValidationError("Provider logout signing key is unknown")

    @staticmethod
    def _bounded_reference(value: Any, *, name: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str) or not value or len(value) > 512:
            raise LogoutTokenValidationError(f"Logout token {name} is invalid")
        return value
