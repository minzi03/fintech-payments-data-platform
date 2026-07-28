"""Bounded outbound OIDC provider adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, perf_counter
from typing import Any
from urllib.parse import urlsplit

import httpx
from opentelemetry.trace import SpanKind

from portal_api.auth.ports import (
    OidcProviderPort,
    ProviderExchangeFailure,
    ProviderFailureKind,
    ProviderOperationResult,
    ProviderOperationStatus,
    ProviderRefreshTokenSet,
    ProviderTokenKind,
    ProviderTokenSet,
)
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.core.config import PortalApiSettings
from portal_api.secret_provider import PortalSecretId, ResolvedSecret, resolve_environment_secrets
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder

MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024
MAX_PROVIDER_TOKEN_BYTES = 64 * 1024


@dataclass(frozen=True)
class _CachedDiscovery:
    provider: OidcProviderConfig
    cached_at: float


@dataclass(frozen=True)
class _CachedJwks:
    payload: dict[str, Any]
    jwks_uri: str
    cached_at: float


class HttpxOidcProvider(OidcProviderPort):
    def __init__(
        self,
        settings: PortalApiSettings,
        *,
        client_secret: ResolvedSecret | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = monotonic,
        telemetry: TelemetryRecorder | None = None,
    ) -> None:
        resolved_client_secret = client_secret or resolve_environment_secrets(settings).require(
            PortalSecretId.OIDC_CLIENT_SECRET
        )
        self._settings = settings
        self._client_secret = resolved_client_secret.reveal_text()
        self._timeout = httpx.Timeout(settings.oidc_http_timeout_seconds)
        self._transport = transport
        self._clock = clock
        self._telemetry = telemetry or NoopTelemetry()
        self._discovery_cache: _CachedDiscovery | None = None
        self._jwks_cache: _CachedJwks | None = None
        self._discovery_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

    async def get_config(self, *, force_refresh: bool = False) -> OidcProviderConfig:
        cached = self._discovery_cache
        now = self._clock()
        if cached is not None and not force_refresh and self._is_fresh(cached.cached_at, now):
            self._telemetry.record_oidc("discovery", "cache_hit", 0)
            return cached.provider

        async with self._discovery_lock:
            cached = self._discovery_cache
            now = self._clock()
            if cached is not None and not force_refresh and self._is_fresh(cached.cached_at, now):
                self._telemetry.record_oidc("discovery", "cache_hit", 0)
                return cached.provider
            try:
                payload = await self._get_json(
                    self._settings.oidc_discovery_url_value,
                    operation="discovery",
                )
                provider = OidcProviderConfig.from_discovery(self._settings, payload)
            except ProviderExchangeFailure as error:
                if (
                    not force_refresh
                    and error.kind is ProviderFailureKind.AMBIGUOUS
                    and cached is not None
                    and self._is_within_stale_ceiling(cached.cached_at, now)
                ):
                    return cached.provider
                raise
            except ValueError as error:
                raise ProviderExchangeFailure(
                    ProviderFailureKind.AUTHORITATIVE_REJECTION
                ) from error

            if cached is not None and cached.provider.jwks_uri != provider.jwks_uri:
                self._jwks_cache = None
            self._discovery_cache = _CachedDiscovery(provider=provider, cached_at=now)
            return provider

    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        started = perf_counter()
        with self._telemetry.span(
            "oidc.token_exchange",
            kind=SpanKind.CLIENT,
            attributes={"oidc.operation": "token_exchange"},
        ):
            try:
                result = await self._exchange_code(
                    code=code,
                    verifier=verifier,
                    redirect_uri=redirect_uri,
                )
            except ProviderExchangeFailure:
                self._telemetry.record_oidc(
                    "token_exchange",
                    "error",
                    (perf_counter() - started) * 1000,
                )
                raise
            self._telemetry.record_oidc(
                "token_exchange",
                "success",
                (perf_counter() - started) * 1000,
            )
            return result

    async def refresh_tokens(self, *, refresh_token: str) -> ProviderRefreshTokenSet:
        if not refresh_token or len(refresh_token.encode("utf-8")) > MAX_PROVIDER_TOKEN_BYTES:
            raise ProviderExchangeFailure(
                ProviderFailureKind.PRE_DISPATCH,
                reason_code="REFRESH_TOKEN_INVALID",
            )
        started = perf_counter()
        outcome = "error"
        with self._telemetry.span(
            "oidc.token_refresh",
            kind=SpanKind.CLIENT,
            attributes={"oidc.operation": "token_refresh"},
        ):
            try:
                provider = await self.get_config()
                response = await self._post_form(
                    provider.token_endpoint,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    provider=provider,
                )
                if response.status_code < 200 or response.status_code >= 300:
                    raise self._failure_from_response(response, operation="refresh")
                payload = self._bounded_object(response)
                access_token = payload["access_token"]
                token_type = payload["token_type"]
                expires_in = payload["expires_in"]
                rotated_refresh_token = payload.get("refresh_token")
                id_token = payload.get("id_token")
                refresh_expires_in = payload.get("refresh_expires_in")
                if (
                    not isinstance(access_token, str)
                    or not access_token
                    or not isinstance(token_type, str)
                    or token_type.casefold() != "bearer"
                    or not isinstance(expires_in, int)
                    or isinstance(expires_in, bool)
                    or expires_in <= 0
                    or (
                        rotated_refresh_token is not None
                        and not isinstance(rotated_refresh_token, str)
                    )
                    or (id_token is not None and not isinstance(id_token, str))
                    or (
                        refresh_expires_in is not None
                        and (
                            not isinstance(refresh_expires_in, int)
                            or isinstance(refresh_expires_in, bool)
                            or refresh_expires_in <= 0
                        )
                    )
                ):
                    raise ProviderExchangeFailure(
                        ProviderFailureKind.AUTHORITATIVE_REJECTION,
                        reason_code="REFRESH_RESPONSE_INVALID",
                    )
                outcome = "success"
                return ProviderRefreshTokenSet(
                    access_token=access_token,
                    refresh_token=rotated_refresh_token,
                    id_token=id_token,
                    token_type=token_type,
                    expires_in=expires_in,
                    refresh_expires_in=refresh_expires_in,
                )
            finally:
                self._telemetry.record_oidc(
                    "token_refresh",
                    outcome,
                    (perf_counter() - started) * 1000,
                )

    async def revoke_token(
        self,
        *,
        token: str,
        token_kind: ProviderTokenKind,
    ) -> ProviderOperationResult:
        if not token or len(token.encode("utf-8")) > MAX_PROVIDER_TOKEN_BYTES:
            raise ProviderExchangeFailure(
                ProviderFailureKind.PRE_DISPATCH,
                reason_code="REVOCATION_TOKEN_INVALID",
            )
        provider = await self.get_config()
        if provider.revocation_endpoint is None:
            return ProviderOperationResult(ProviderOperationStatus.UNSUPPORTED)
        started = perf_counter()
        outcome = "error"
        with self._telemetry.span(
            "oidc.token_revocation",
            kind=SpanKind.CLIENT,
            attributes={
                "oidc.operation": "token_revocation",
                "oauth.token.type": token_kind.value,
            },
        ):
            try:
                response = await self._post_form(
                    provider.revocation_endpoint,
                    data={
                        "token": token,
                        "token_type_hint": token_kind.value,
                    },
                    provider=provider,
                )
                if response.status_code < 200 or response.status_code >= 300:
                    raise self._failure_from_response(response, operation="revocation")
                outcome = "success"
                return ProviderOperationResult(ProviderOperationStatus.SUCCEEDED)
            finally:
                self._telemetry.record_oidc(
                    "token_revocation",
                    outcome,
                    (perf_counter() - started) * 1000,
                )

    async def logout_provider_session(
        self,
        *,
        refresh_token: str | None,
    ) -> ProviderOperationResult:
        provider = await self.get_config()
        if provider.end_session_endpoint is None or refresh_token is None:
            return ProviderOperationResult(ProviderOperationStatus.UNSUPPORTED)
        if not refresh_token or len(refresh_token.encode("utf-8")) > MAX_PROVIDER_TOKEN_BYTES:
            raise ProviderExchangeFailure(
                ProviderFailureKind.PRE_DISPATCH,
                reason_code="LOGOUT_TOKEN_INVALID",
            )
        started = perf_counter()
        outcome = "error"
        with self._telemetry.span(
            "oidc.provider_logout",
            kind=SpanKind.CLIENT,
            attributes={"oidc.operation": "provider_logout"},
        ):
            try:
                response = await self._post_form(
                    provider.end_session_endpoint,
                    data={"refresh_token": refresh_token},
                    provider=provider,
                )
                if response.status_code < 200 or response.status_code >= 300:
                    raise self._failure_from_response(response, operation="logout")
                outcome = "success"
                return ProviderOperationResult(ProviderOperationStatus.SUCCEEDED)
            finally:
                self._telemetry.record_oidc(
                    "provider_logout",
                    outcome,
                    (perf_counter() - started) * 1000,
                )

    async def front_channel_logout_url(self) -> str | None:
        provider = await self.get_config()
        return provider.end_session_endpoint

    async def _exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        provider = await self.get_config()
        if redirect_uri != provider.redirect_uri:
            raise ProviderExchangeFailure(ProviderFailureKind.PRE_DISPATCH)
        headers = {"Accept": "application/json"}
        self._telemetry.inject(headers)
        try:
            async with self._client() as client:
                response = await client.post(
                    provider.token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": provider.redirect_uri,
                        "code_verifier": verifier,
                    },
                    auth=httpx.BasicAuth(provider.client_id, self._client_secret),
                    headers=headers,
                )
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as error:
            raise ProviderExchangeFailure(ProviderFailureKind.PRE_DISPATCH) from error
        except httpx.RequestError as error:
            raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS) from error
        if len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
        if response.status_code < 200 or response.status_code >= 300:
            raise self._failure_from_response(response, operation="exchange")
        try:
            payload = self._bounded_object(response)
            id_token = payload["id_token"]
            token_type = payload["token_type"]
            if not isinstance(id_token, str) or not isinstance(token_type, str):
                raise TypeError
            if token_type.casefold() != "bearer":
                raise TypeError
            access_token = payload.get("access_token")
            refresh_token = payload.get("refresh_token")
            expires_in = payload.get("expires_in")
            refresh_expires_in = payload.get("refresh_expires_in")
            if access_token is not None and not isinstance(access_token, str):
                raise TypeError
            if refresh_token is not None and not isinstance(refresh_token, str):
                raise TypeError
            if expires_in is not None and (
                not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0
            ):
                raise TypeError
            if refresh_expires_in is not None and (
                not isinstance(refresh_expires_in, int)
                or isinstance(refresh_expires_in, bool)
                or refresh_expires_in <= 0
            ):
                raise TypeError
        except (KeyError, TypeError, ValueError) as error:
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION) from error
        return ProviderTokenSet(
            id_token=id_token,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            expires_in=expires_in,
            refresh_expires_in=refresh_expires_in,
        )

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]:
        provider = await self.get_config()
        cached = self._jwks_cache
        now = self._clock()
        if (
            cached is not None
            and cached.jwks_uri == provider.jwks_uri
            and not force_refresh
            and self._is_fresh(cached.cached_at, now)
        ):
            self._telemetry.record_oidc("jwks", "cache_hit", 0)
            return cached.payload

        async with self._jwks_lock:
            cached = self._jwks_cache
            now = self._clock()
            if (
                cached is not None
                and cached.jwks_uri == provider.jwks_uri
                and not force_refresh
                and self._is_fresh(cached.cached_at, now)
            ):
                self._telemetry.record_oidc("jwks", "cache_hit", 0)
                return cached.payload
            try:
                payload = await self._get_json(provider.jwks_uri, operation="jwks")
                if not isinstance(payload.get("keys"), list):
                    raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
            except ProviderExchangeFailure as error:
                if (
                    not force_refresh
                    and error.kind is ProviderFailureKind.AMBIGUOUS
                    and cached is not None
                    and cached.jwks_uri == provider.jwks_uri
                    and self._is_within_stale_ceiling(cached.cached_at, now)
                ):
                    return cached.payload
                raise
            self._jwks_cache = _CachedJwks(
                payload=payload,
                jwks_uri=provider.jwks_uri,
                cached_at=now,
            )
            return payload

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self._timeout,
            transport=self._transport,
            follow_redirects=False,
        )

    async def _post_form(
        self,
        url: str,
        *,
        data: dict[str, str],
        provider: OidcProviderConfig,
    ) -> httpx.Response:
        headers = {"Accept": "application/json"}
        self._telemetry.inject(headers)
        try:
            async with self._client() as client:
                response = await client.post(
                    url,
                    data=data,
                    auth=httpx.BasicAuth(provider.client_id, self._client_secret),
                    headers=headers,
                )
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as error:
            raise ProviderExchangeFailure(
                ProviderFailureKind.PRE_DISPATCH,
                reason_code="PROVIDER_ENDPOINT_INVALID",
            ) from error
        except httpx.TimeoutException as error:
            raise ProviderExchangeFailure(
                ProviderFailureKind.AMBIGUOUS,
                reason_code="PROVIDER_TIMEOUT",
            ) from error
        except httpx.RequestError as error:
            raise ProviderExchangeFailure(
                ProviderFailureKind.AMBIGUOUS,
                reason_code="PROVIDER_UNAVAILABLE",
            ) from error
        if len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderExchangeFailure(
                ProviderFailureKind.AUTHORITATIVE_REJECTION,
                reason_code="PROVIDER_RESPONSE_TOO_LARGE",
            )
        return response

    @staticmethod
    def _bounded_object(response: httpx.Response) -> dict[str, Any]:
        if len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderExchangeFailure(
                ProviderFailureKind.AUTHORITATIVE_REJECTION,
                reason_code="PROVIDER_RESPONSE_TOO_LARGE",
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderExchangeFailure(
                ProviderFailureKind.AUTHORITATIVE_REJECTION,
                reason_code="PROVIDER_RESPONSE_INVALID",
            ) from error
        if not isinstance(payload, dict):
            raise ProviderExchangeFailure(
                ProviderFailureKind.AUTHORITATIVE_REJECTION,
                reason_code="PROVIDER_RESPONSE_INVALID",
            )
        return payload

    @classmethod
    def _failure_from_response(
        cls,
        response: httpx.Response,
        *,
        operation: str,
    ) -> ProviderExchangeFailure:
        reason = f"PROVIDER_{operation.upper()}_REJECTED"
        try:
            payload = cls._bounded_object(response)
        except ProviderExchangeFailure:
            payload = {}
        provider_error = payload.get("error")
        if provider_error == "invalid_grant":
            reason = "INVALID_GRANT"
        elif provider_error == "invalid_client":
            reason = "INVALID_CLIENT"
        elif response.status_code >= 500:
            return ProviderExchangeFailure(
                ProviderFailureKind.AMBIGUOUS,
                reason_code="PROVIDER_UNAVAILABLE",
            )
        return ProviderExchangeFailure(
            ProviderFailureKind.AUTHORITATIVE_REJECTION,
            reason_code=reason,
        )

    async def _get_json(self, url: str, *, operation: str) -> dict[str, Any]:
        started = perf_counter()
        outcome = "error"
        headers = {"Accept": "application/json"}
        self._telemetry.inject(headers)
        authority = urlsplit(url).hostname or "invalid"
        try:
            with self._telemetry.span(
                f"oidc.{operation}",
                kind=SpanKind.CLIENT,
                attributes={
                    "oidc.operation": operation,
                    "server.address": authority,
                },
            ):
                try:
                    async with self._client() as client:
                        response = await client.get(url, headers=headers)
                except (httpx.InvalidURL, httpx.UnsupportedProtocol) as error:
                    raise ProviderExchangeFailure(ProviderFailureKind.PRE_DISPATCH) from error
                except httpx.RequestError as error:
                    raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS) from error
                if response.status_code >= 500:
                    raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS)
                if (
                    response.status_code != 200
                    or len(response.content) > MAX_PROVIDER_RESPONSE_BYTES
                ):
                    raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
                try:
                    payload = response.json()
                except ValueError as error:
                    raise ProviderExchangeFailure(
                        ProviderFailureKind.AUTHORITATIVE_REJECTION
                    ) from error
                if not isinstance(payload, dict):
                    raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
                outcome = "success"
                return payload
        finally:
            self._telemetry.record_oidc(
                operation,
                outcome,
                (perf_counter() - started) * 1000,
                cache_refresh=True,
            )

    def _is_fresh(self, cached_at: float, now: float) -> bool:
        return now - cached_at < self._settings.oidc_cache_ttl_seconds

    def _is_within_stale_ceiling(self, cached_at: float, now: float) -> bool:
        return now - cached_at <= self._settings.oidc_stale_ceiling_seconds
