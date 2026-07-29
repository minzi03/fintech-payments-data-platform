"""Abuse-protection configuration is explicit, bounded, and fail-fast."""

from __future__ import annotations

import pytest
from portal_api.abuse.client_address import ForwardedHeaderMode
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from pydantic import ValidationError

SECRET = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="


def _enabled(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "environment": PortalEnvironment.TEST,
        "abuse_protection_enabled": True,
        "client_address_hmac_secret": SECRET,
        "redis_url": "redis://localhost:6379/0",
    }
    values.update(updates)
    return values


def test_enabled_abuse_configuration_materializes_bounded_values() -> None:
    settings = PortalApiSettings(**_enabled())

    assert settings.client_address_hmac_secret_bytes == bytes(range(32))
    assert settings.redis_operation_timeout_seconds <= 5
    assert settings.abuse_local_fallback_max_keys <= 100_000


@pytest.mark.parametrize(
    "updates",
    [
        {"client_address_hmac_secret": None},
        {"client_address_hmac_secret": "c2hvcnQ="},
        {"redis_url": None},
        {"redis_url": "http://localhost:6379"},
        {"redis_url": "redis://localhost:6379/0?unsafe=true"},
        {"trusted_proxy_cidrs": "0.0.0.0/0"},
        {"trusted_proxy_cidrs": "10.0.0.1/8"},
        {
            "forwarded_header_mode": ForwardedHeaderMode.X_FORWARDED_FOR,
            "trusted_proxy_cidrs": "",
        },
    ],
)
def test_unsafe_abuse_configuration_is_rejected(updates: dict[str, object]) -> None:
    with pytest.raises((ValidationError, ValueError)):
        PortalApiSettings(**_enabled(**updates))
