"""Pre-authentication cookie policy without browser authority."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Response

from portal_api.core.config import PortalApiSettings, PortalEnvironment

LOCAL_BINDING_COOKIE = "fintech_portal_oidc_binding_v1"
SECURE_BINDING_COOKIE = "__Host-fintech_portal_oidc_binding_v1"
LOCAL_SESSION_COOKIE = "fintech_portal_session_v1"
SECURE_SESSION_COOKIE = "__Host-fintech_portal_session_v1"


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


def clear_browser_binding_cookie(
    response: Response,
    *,
    settings: PortalApiSettings,
) -> None:
    policy = browser_binding_cookie(settings)
    response.delete_cookie(
        key=policy.name,
        path="/",
        secure=policy.secure,
        httponly=True,
        samesite="lax",
    )


def set_session_cookie(
    response: Response,
    *,
    settings: PortalApiSettings,
    session_secret: str,
    absolute_expires_at: datetime,
) -> None:
    local_exception = settings.environment in {
        PortalEnvironment.LOCAL,
        PortalEnvironment.TEST,
    }
    remaining = max(0, int((absolute_expires_at - datetime.now(UTC)).total_seconds()))
    response.set_cookie(
        key=LOCAL_SESSION_COOKIE if local_exception else SECURE_SESSION_COOKIE,
        value=session_secret,
        max_age=min(remaining, settings.session_absolute_ttl_seconds),
        path="/",
        secure=not local_exception,
        httponly=True,
        samesite="lax",
    )
