"""Canonical public environment-alias inventory."""

from __future__ import annotations

from collections.abc import Iterable

PORTAL_API_PREFIX = "PORTAL_API_"

PORTAL_API_SECRET_FIELDS = frozenset(
    {
        "audit_worker_database_url",
        "client_address_hmac_secret",
        "database_url",
        "oidc_client_secret",
        "redis_url",
        "security_master_key",
        "security_previous_master_key",
    }
)


def environment_alias(field_name: str) -> str:
    return f"{PORTAL_API_PREFIX}{field_name.upper()}"


def supported_environment_aliases(field_names: Iterable[str]) -> frozenset[str]:
    """Return the stable flat alias boundary for Portal API configuration."""
    return frozenset(environment_alias(name) for name in field_names)


def unknown_prefixed_environment_names(
    environment_names: Iterable[str],
    *,
    supported: frozenset[str],
) -> tuple[str, ...]:
    """Return safe names in deterministic order."""
    supported_folded = {name.casefold() for name in supported}
    return tuple(
        sorted(
            {
                name
                for name in environment_names
                if name.casefold().startswith(PORTAL_API_PREFIX.casefold())
                and name.casefold() not in supported_folded
            },
            key=str.casefold,
        )
    )
