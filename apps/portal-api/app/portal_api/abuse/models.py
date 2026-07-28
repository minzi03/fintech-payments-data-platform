"""Bounded abuse-protection contracts without infrastructure dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class AbuseOperation(StrEnum):
    LOGIN_INITIATION = "login_initiation"
    OIDC_CALLBACK = "oidc_callback"
    FOREGROUND_REFRESH = "foreground_refresh"
    BACKGROUND_REFRESH = "background_refresh"
    LOGOUT = "logout"
    PROVIDER_END_SESSION = "provider_end_session"
    ACCESS_TOKEN_REVOCATION = "access_token_revocation"
    REFRESH_TOKEN_REVOCATION = "refresh_token_revocation"
    BACKCHANNEL_LOGOUT = "backchannel_logout"
    INVALID_AUTH_TRAFFIC = "invalid_auth_traffic"


class AbuseDecision(StrEnum):
    ALLOW = "allow"
    THROTTLE = "throttle"
    TEMPORARILY_BLOCK = "temporarily_block"
    DEGRADED_ALLOW = "degraded_allow"
    DEGRADED_DENY = "degraded_deny"

    @property
    def permits_operation(self) -> bool:
        return self in {AbuseDecision.ALLOW, AbuseDecision.DEGRADED_ALLOW}


class AbuseDimension(StrEnum):
    IP_PREFIX = "ip_prefix"
    SESSION = "session"
    SUBJECT = "subject"
    PROVIDER_ISSUER = "provider_issuer"
    PROVIDER_CLIENT = "provider_client"
    PROVIDER_SESSION = "provider_session"
    BACKCHANNEL_JTI = "backchannel_jti"
    GLOBAL_OPERATION = "global_operation"


class AbuseBackendStatus(StrEnum):
    UP = "up"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"


class PenaltyLevel(StrEnum):
    NORMAL = "normal"
    THROTTLED = "throttled"
    TEMPORARILY_BLOCKED = "temporarily_blocked"
    EXTENDED_BLOCK = "extended_block"


class AbuseFailureMode(StrEnum):
    LOCAL_FALLBACK_ALLOW = "local_fallback_allow"
    LOCAL_FALLBACK_DENY = "local_fallback_deny"
    ALWAYS_ALLOW = "always_allow"


@dataclass(frozen=True, slots=True)
class ResolvedClientAddress:
    """Privacy-safe client address identity carried through request state."""

    fingerprint: str
    address_family: str
    source: str


@dataclass(frozen=True, slots=True)
class AbuseBucketRequest:
    key: str
    dimension: AbuseDimension
    capacity: int
    refill_tokens: int
    refill_period_ms: int
    request_cost: int
    state_ttl_ms: int
    penalty_base_ms: int
    penalty_max_level: int
    penalty_decay_ms: int


@dataclass(frozen=True, slots=True)
class AbuseEvaluationRequest:
    operation: AbuseOperation
    policy_name: str
    policy_version: str
    buckets: tuple[AbuseBucketRequest, ...]


@dataclass(frozen=True, slots=True)
class AbuseEvaluationResult:
    decision: AbuseDecision
    policy_name: str
    policy_version: str
    limiting_dimension: AbuseDimension | None
    retry_after_seconds: int
    remaining: int
    backend_status: AbuseBackendStatus
    penalty_level: PenaltyLevel


@dataclass(frozen=True, slots=True)
class AbuseConcurrencyRequest:
    key: str
    operation: AbuseOperation
    limit: int
    lease_ms: int
    owner_token: str


@dataclass(frozen=True, slots=True)
class AbuseConcurrencyLease:
    key: str
    owner_token: str
    expires_in_ms: int
