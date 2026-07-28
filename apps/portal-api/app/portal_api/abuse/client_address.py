"""Trusted-proxy client address resolution and privacy-safe fingerprinting."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from portal_api.abuse.models import ResolvedClientAddress


class ForwardedHeaderMode(StrEnum):
    DIRECT = "direct"
    X_FORWARDED_FOR = "x_forwarded_for"


@dataclass(frozen=True, slots=True)
class ClientAddressSettings:
    trusted_proxy_cidrs: tuple[str, ...]
    forwarded_header_mode: ForwardedHeaderMode
    max_forwarded_hops: int
    ipv4_prefix_length: int
    ipv6_prefix_length: int
    fingerprint_key: bytes


class TrustedClientAddressResolver:
    """Resolve the first untrusted hop without trusting caller-controlled headers."""

    def __init__(self, settings: ClientAddressSettings) -> None:
        if len(settings.fingerprint_key) < 32:
            raise ValueError("Client address fingerprint key must contain at least 256 bits")
        self._settings = settings
        self._trusted = tuple(
            ipaddress.ip_network(value, strict=True) for value in settings.trusted_proxy_cidrs
        )

    def resolve_scope(
        self,
        scope: dict[str, Any],
        headers: dict[str, str],
    ) -> ResolvedClientAddress:
        client = scope.get("client")
        peer_value = client[0] if isinstance(client, tuple) and client else "127.0.0.1"
        try:
            peer = self._parse(peer_value)
        except ValueError:
            # ASGI test transports and defensive adapters can provide a
            # non-address peer label. Collapse all such values into one safe
            # bucket instead of trusting or recording the raw label.
            peer = ipaddress.ip_address("0.0.0.0")
        selected = peer
        source = "socket"
        if (
            self._settings.forwarded_header_mode is ForwardedHeaderMode.X_FORWARDED_FOR
            and self._is_trusted(peer)
        ):
            forwarded = headers.get("x-forwarded-for")
            parsed = self._parse_forwarded(forwarded)
            if parsed is not None:
                selected = self._select_client(parsed, peer)
                source = "trusted_proxy"
        network = self._aggregate(selected)
        digest = hmac.new(
            self._settings.fingerprint_key,
            f"portal-client-address-v1\0{network.with_prefixlen}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return ResolvedClientAddress(
            fingerprint=digest,
            address_family="ipv4" if network.version == 4 else "ipv6",
            source=source,
        )

    def fingerprint_value(self, purpose: str, value: str) -> str:
        return hmac.new(
            self._settings.fingerprint_key,
            f"portal-abuse-{purpose}-v1\0{value}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def _parse_forwarded(
        self,
        value: str | None,
    ) -> tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...] | None:
        if not value:
            return None
        raw = tuple(item.strip() for item in value.split(","))
        if not raw or len(raw) > self._settings.max_forwarded_hops or any(not item for item in raw):
            return None
        try:
            return tuple(self._parse(item) for item in raw)
        except ValueError:
            return None

    def _select_client(
        self,
        forwarded: tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
        peer: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        chain = (*forwarded, peer)
        for address in reversed(chain):
            if not self._is_trusted(address):
                return address
        return forwarded[0]

    def _aggregate(
        self,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
        prefix = (
            self._settings.ipv4_prefix_length
            if address.version == 4
            else self._settings.ipv6_prefix_length
        )
        return ipaddress.ip_network(f"{address}/{prefix}", strict=False)

    def _is_trusted(self, address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        return any(
            address.version == network.version and address in network for network in self._trusted
        )

    @staticmethod
    def _parse(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        parsed = ipaddress.ip_address(value)
        if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped is not None:
            return parsed.ipv4_mapped
        return parsed
