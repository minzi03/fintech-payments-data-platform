"""Pre-authentication cookie policy without browser authority."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Response

from portal_api.core.config import PortalApiSettings, PortalEnvironment

LOCAL_BINDING_COOKIE = "fintech_portal_oidc_binding_v1"
SECURE_BINDING_COOKIE = "__Host-fintech_portal_oidc_binding_v1"


@dataclass(frozen=True)
class BrowserBindingCookie:
    name: str
    secure: bool
    max_age: int


def browser_binding_cookie(settings: PortalApiSettings) -> BrowserBindingCookie:
    local_exception = settings.environment in {PortalEnvironment.LOCAL, PortalEnvironment.TEST}
    return BrowserBindingCookie(
        name=LOCAL_BINDING_COOKIE if local_exception else SECURE_BINDING_COOKIE,
        secure=not local_exception,
        max_age=settings.login_transaction_ttl_seconds,
    )


def set_browser_binding_cookie(
    response: Response,
    *,
    settings: PortalApiSettings,
    binding_secret: str,
) -> None:
    policy = browser_binding_cookie(settings)
    response.set_cookie(
        key=policy.name,
        value=binding_secret,
        max_age=policy.max_age,
        path="/",
        secure=policy.secure,
        httponly=True,
        samesite="lax",
    )
