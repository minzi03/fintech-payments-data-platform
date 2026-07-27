"""Pre-authentication cookie policy tests."""

from __future__ import annotations

from portal_api.auth.cookies import (
    LOCAL_BINDING_COOKIE,
    SECURE_BINDING_COOKIE,
    browser_binding_cookie,
)
from portal_api.core.config import PortalApiSettings, PortalEnvironment


def test_local_binding_cookie_is_explicitly_non_secure_for_http_loopback() -> None:
    policy = browser_binding_cookie(
        PortalApiSettings(environment=PortalEnvironment.TEST, login_transaction_ttl_seconds=120)
    )

    assert policy.name == LOCAL_BINDING_COOKIE
    assert not policy.secure
    assert policy.max_age == 120


def test_development_binding_cookie_uses_host_prefix_and_secure_transport() -> None:
    policy = browser_binding_cookie(
        PortalApiSettings(
            environment=PortalEnvironment.DEVELOPMENT,
            allowed_origins="https://portal.dev.example",
        )
    )

    assert policy.name == SECURE_BINDING_COOKIE
    assert policy.secure
