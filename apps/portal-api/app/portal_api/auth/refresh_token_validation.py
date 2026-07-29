"""Validate provider identity continuity on OAuth refresh responses."""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime

import jwt
from opentelemetry.trace import SpanKind

from portal_api.auth.ports import OidcProviderPort
from portal_api.core.config import PortalApiSettings
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder

MAX_REFRESH_ID_TOKEN_BYTES = 64 * 1024
MAX_REFRESH_CLAIMS_BYTES = 32 * 1024
ALLOWED_CLOCK_SKEW_SECONDS = 60


class RefreshIdentityValidationError(ValueError):
    """Sanitized refreshed-ID-token identity continuity failure."""


class ProviderRefreshIdentityValidator:
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

    async def validate(
        self,
        *,
        id_token: str,
        expected_subject: str,
        expected_provider_session: str | None,
    ) -> None:
        with self._telemetry.span(
            "oidc.refresh_identity_validation",
            kind=SpanKind.INTERNAL,
            attributes={"oidc.operation": "refresh_identity_validation"},
        ):
            try:
                await self._validate(
                    id_token=id_token,
                    expected_subject=expected_subject,
                    expected_provider_session=expected_provider_session,
                )
            except RefreshIdentityValidationError:
                self._telemetry.record_oidc_validation_failure("refresh_id_token")
                raise

    async def _validate(
        self,
        *,
        id_token: str,
        expected_subject: str,
        expected_provider_session: str | None,
    ) -> None:
        if len(id_token.encode("utf-8")) > MAX_REFRESH_ID_TOKEN_BYTES:
            raise RefreshIdentityValidationError("Refreshed ID token exceeds the accepted bound")
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as error:
            raise RefreshIdentityValidationError("Refreshed ID token header is invalid") from error
        algorithm = header.get("alg")
        key_id = header.get("kid")
        if (
            not isinstance(algorithm, str)
            or algorithm not in self._settings.oidc_allowed_algorithm_values
            or not isinstance(key_id, str)
            or not key_id
            or header.get("typ") not in {None, "JWT"}
        ):
            raise RefreshIdentityValidationError("Refreshed ID token header is not allowed")
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
                    "require": ["iss", "sub", "aud", "exp", "iat"],
                    "verify_signature": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "verify_exp": True,
                    "verify_iat": True,
                },
            )
        except jwt.PyJWTError as error:
            raise RefreshIdentityValidationError("Refreshed ID token claims are invalid") from error
        encoded_claims = json.dumps(claims, separators=(",", ":")).encode("utf-8")
        if len(encoded_claims) > MAX_REFRESH_CLAIMS_BYTES:
            raise RefreshIdentityValidationError(
                "Refreshed ID token claims exceed the accepted bound"
            )
        subject = claims.get("sub")
        provider_session = claims.get("sid")
        if (
            not isinstance(subject, str)
            or not hmac.compare_digest(subject, expected_subject)
            or (
                expected_provider_session is not None
                and (
                    not isinstance(provider_session, str)
                    or not hmac.compare_digest(
                        provider_session,
                        expected_provider_session,
                    )
                )
            )
        ):
            raise RefreshIdentityValidationError(
                "Refreshed ID token changed provider identity authority"
            )
        expiration = claims.get("exp")
        if (
            not isinstance(expiration, int)
            or isinstance(expiration, bool)
            or datetime.fromtimestamp(expiration, tz=UTC) <= datetime.now(UTC)
        ):
            raise RefreshIdentityValidationError("Refreshed ID token expiry is invalid")

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
                        raise RefreshIdentityValidationError(
                            "Provider refresh signing key is invalid"
                        ) from error
        raise RefreshIdentityValidationError("Provider refresh signing key is unknown")
