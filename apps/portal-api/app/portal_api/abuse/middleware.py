"""ASGI middleware that resolves privacy-safe client identity only."""

from __future__ import annotations

from typing import Any

from portal_api.abuse.client_address import TrustedClientAddressResolver


class AbuseContextMiddleware:
    def __init__(self, app: Any, resolver: TrustedClientAddressResolver) -> None:
        self.app = app
        self._resolver = resolver

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http":
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            state = scope.setdefault("state", {})
            state["abuse_client"] = self._resolver.resolve_scope(scope, headers)
        await self.app(scope, receive, send)
