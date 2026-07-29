"""Durable security state names shared across repositories."""

from __future__ import annotations

from enum import StrEnum


class LoginTransactionStatus(StrEnum):
    PENDING = "PENDING"
    CLAIMED = "CLAIMED"
    CONSUMED = "CONSUMED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"


class SessionStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REFRESH_REQUIRED = "REFRESH_REQUIRED"
    EXPIRED_IDLE = "EXPIRED_IDLE"
    EXPIRED_ABSOLUTE = "EXPIRED_ABSOLUTE"
    REVOKED = "REVOKED"
    PROVIDER_REVOKED = "PROVIDER_REVOKED"
    INVALID = "INVALID"
    TERMINATED = "TERMINATED"


class PrincipalStatus(StrEnum):
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
