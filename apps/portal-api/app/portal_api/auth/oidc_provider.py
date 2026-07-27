"""Bounded outbound OIDC provider adapter."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any

import httpx

from portal_api.auth.ports import (
    OidcProviderPort,
    ProviderExchangeFailure,
    ProviderFailureKind,
    ProviderTokenSet,
)
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.core.config import PortalApiSettings

MAX_PROVIDER_RESPONSE_BYTES = 64 * 1024


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
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if settings.oidc_client_secret is None:
            raise RuntimeError("Validated OIDC client credentials are unavailable")
        self._settings = settings
        self._client_secret = settings.oidc_client_secret.get_secret_value()
        self._timeout = httpx.Timeout(settings.oidc_http_timeout_seconds)
        self._transport = transport
        self._clock = clock
        self._discovery_cache: _CachedDiscovery | None = None
        self._jwks_cache: _CachedJwks | None = None
        self._discovery_lock = asyncio.Lock()
        self._jwks_lock = asyncio.Lock()

    async def get_config(self, *, force_refresh: bool = False) -> OidcProviderConfig:
        cached = self._discovery_cache
        now = self._clock()
        if cached is not None and not force_refresh and self._is_fresh(cached.cached_at, now):
            return cached.provider

        async with self._discovery_lock:
            cached = self._discovery_cache
            now = self._clock()
            if cached is not None and not force_refresh and self._is_fresh(cached.cached_at, now):
                return cached.provider
            try:
                payload = await self._get_json(self._settings.oidc_discovery_url_value)
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
        provider = await self.get_config()
        if redirect_uri != provider.redirect_uri:
            raise ProviderExchangeFailure(ProviderFailureKind.PRE_DISPATCH)
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
                    headers={"Accept": "application/json"},
                )
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as error:
            raise ProviderExchangeFailure(ProviderFailureKind.PRE_DISPATCH) from error
        except httpx.RequestError as error:
            raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS) from error
        if len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
        if response.status_code < 200 or response.status_code >= 300:
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
        try:
            payload = response.json()
            id_token = payload["id_token"]
            token_type = payload["token_type"]
            if not isinstance(id_token, str) or not isinstance(token_type, str):
                raise TypeError
            if token_type.casefold() != "bearer":
                raise TypeError
            access_token = payload.get("access_token")
            refresh_token = payload.get("refresh_token")
            expires_in = payload.get("expires_in")
            if access_token is not None and not isinstance(access_token, str):
                raise TypeError
            if refresh_token is not None and not isinstance(refresh_token, str):
                raise TypeError
            if expires_in is not None and (
                not isinstance(expires_in, int) or isinstance(expires_in, bool) or expires_in <= 0
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
                return cached.payload
            try:
                payload = await self._get_json(provider.jwks_uri)
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

    async def _get_json(self, url: str) -> dict[str, Any]:
        try:
            async with self._client() as client:
                response = await client.get(url, headers={"Accept": "application/json"})
        except (httpx.InvalidURL, httpx.UnsupportedProtocol) as error:
            raise ProviderExchangeFailure(ProviderFailureKind.PRE_DISPATCH) from error
        except httpx.RequestError as error:
            raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS) from error
        if response.status_code >= 500:
            raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS)
        if response.status_code != 200 or len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION) from error
        if not isinstance(payload, dict):
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
        return payload

    def _is_fresh(self, cached_at: float, now: float) -> bool:
        return now - cached_at < self._settings.oidc_cache_ttl_seconds

    def _is_within_stale_ceiling(self, cached_at: float, now: float) -> bool:
        return now - cached_at <= self._settings.oidc_stale_ceiling_seconds
