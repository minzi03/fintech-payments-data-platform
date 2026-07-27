"""Bounded outbound OIDC provider adapter."""

from __future__ import annotations

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


class HttpxOidcProvider(OidcProviderPort):
    def __init__(self, settings: PortalApiSettings) -> None:
        self._provider = OidcProviderConfig.from_settings(settings)
        self._timeout = httpx.Timeout(settings.oidc_http_timeout_seconds)
        self._cached_jwks: dict[str, Any] | None = None

    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet:
        if redirect_uri != self._provider.redirect_uri:
            raise ProviderExchangeFailure(ProviderFailureKind.PRE_DISPATCH)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    self._provider.token_endpoint,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "client_id": self._provider.client_id,
                        "redirect_uri": self._provider.redirect_uri,
                        "code_verifier": verifier,
                    },
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
        if self._cached_jwks is not None and not force_refresh:
            return self._cached_jwks
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    self._provider.jwks_uri,
                    headers={"Accept": "application/json"},
                )
        except httpx.RequestError as error:
            raise ProviderExchangeFailure(ProviderFailureKind.AMBIGUOUS) from error
        if response.status_code != 200 or len(response.content) > MAX_PROVIDER_RESPONSE_BYTES:
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("keys"), list):
            raise ProviderExchangeFailure(ProviderFailureKind.AUTHORITATIVE_REJECTION)
        self._cached_jwks = payload
        return payload
