"""Typed and bounded abuse-protection policy registry."""

from __future__ import annotations

import re
from dataclasses import dataclass

from portal_api.abuse.models import (
    AbuseDimension,
    AbuseFailureMode,
    AbuseOperation,
)

_POLICY_ID = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")


@dataclass(frozen=True, slots=True)
class BucketPolicy:
    capacity: int
    refill_tokens: int
    refill_period_seconds: float
    request_cost: int
    state_ttl_seconds: int

    def validate(self) -> None:
        if not 1 <= self.capacity <= 1_000_000:
            raise ValueError("Abuse bucket capacity must be between 1 and 1,000,000")
        if not 1 <= self.refill_tokens <= self.capacity:
            raise ValueError("Abuse bucket refill must be positive and no greater than capacity")
        if not 0.1 <= self.refill_period_seconds <= 86_400:
            raise ValueError("Abuse bucket refill period is outside the bounded range")
        if not 1 <= self.request_cost <= self.capacity:
            raise ValueError("Abuse request cost must be positive and no greater than capacity")
        if not 1 <= self.state_ttl_seconds <= 86_400:
            raise ValueError("Abuse bucket state TTL is outside the bounded range")


@dataclass(frozen=True, slots=True)
class PenaltyPolicy:
    base_block_seconds: int = 15
    max_level: int = 3
    decay_seconds: int = 300

    def validate(self) -> None:
        if not 1 <= self.base_block_seconds <= 3_600:
            raise ValueError("Abuse penalty base block must be bounded")
        if not 1 <= self.max_level <= 3:
            raise ValueError("Abuse penalty maximum level must be between 1 and 3")
        if not 1 <= self.decay_seconds <= 86_400:
            raise ValueError("Abuse penalty decay must be bounded")


@dataclass(frozen=True, slots=True)
class DimensionPolicy:
    dimension: AbuseDimension
    bucket: BucketPolicy


@dataclass(frozen=True, slots=True)
class OperationPolicy:
    name: str
    operation: AbuseOperation
    dimensions: tuple[DimensionPolicy, ...]
    backend_failure_mode: AbuseFailureMode
    penalty: PenaltyPolicy

    def validate(self) -> None:
        if _POLICY_ID.fullmatch(self.name) is None:
            raise ValueError("Abuse policy name must be a bounded safe identifier")
        if not self.dimensions:
            raise ValueError("Abuse policy must define at least one dimension")
        seen: set[AbuseDimension] = set()
        for item in self.dimensions:
            if item.dimension in seen:
                raise ValueError(f"Duplicate abuse dimension: {item.dimension.value}")
            seen.add(item.dimension)
            item.bucket.validate()
        self.penalty.validate()


class AbusePolicyRegistry:
    def __init__(self, policies: tuple[OperationPolicy, ...]) -> None:
        self._policies: dict[AbuseOperation, OperationPolicy] = {}
        names: set[str] = set()
        for policy in policies:
            policy.validate()
            if policy.operation in self._policies:
                raise ValueError(f"Duplicate abuse operation: {policy.operation.value}")
            if policy.name in names:
                raise ValueError(f"Duplicate abuse policy name: {policy.name}")
            names.add(policy.name)
            self._policies[policy.operation] = policy

    def get(self, operation: AbuseOperation) -> OperationPolicy:
        try:
            return self._policies[operation]
        except KeyError as error:
            raise ValueError(f"No abuse policy configured for {operation.value}") from error

    @classmethod
    def development_defaults(cls) -> AbusePolicyRegistry:
        def bucket(
            dimension: AbuseDimension,
            *,
            capacity: int,
            refill: int,
            period: float,
            cost: int,
            ttl: int = 900,
        ) -> DimensionPolicy:
            return DimensionPolicy(
                dimension=dimension,
                bucket=BucketPolicy(capacity, refill, period, cost, ttl),
            )

        standard_penalty = PenaltyPolicy()
        conservative_penalty = PenaltyPolicy(base_block_seconds=30, max_level=3, decay_seconds=600)
        policies = (
            OperationPolicy(
                "login-v1",
                AbuseOperation.LOGIN_INITIATION,
                (
                    bucket(AbuseDimension.IP_PREFIX, capacity=20, refill=10, period=60, cost=2),
                    bucket(
                        AbuseDimension.GLOBAL_OPERATION,
                        capacity=400,
                        refill=200,
                        period=60,
                        cost=2,
                    ),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "callback-v1",
                AbuseOperation.OIDC_CALLBACK,
                (
                    bucket(AbuseDimension.IP_PREFIX, capacity=40, refill=20, period=60, cost=1),
                    bucket(
                        AbuseDimension.PROVIDER_ISSUER,
                        capacity=300,
                        refill=150,
                        period=60,
                        cost=1,
                    ),
                    bucket(
                        AbuseDimension.GLOBAL_OPERATION,
                        capacity=600,
                        refill=300,
                        period=60,
                        cost=1,
                    ),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "invalid-auth-v1",
                AbuseOperation.INVALID_AUTH_TRAFFIC,
                (
                    bucket(AbuseDimension.IP_PREFIX, capacity=20, refill=5, period=60, cost=5),
                    bucket(
                        AbuseDimension.GLOBAL_OPERATION,
                        capacity=200,
                        refill=50,
                        period=60,
                        cost=5,
                    ),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_DENY,
                conservative_penalty,
            ),
            OperationPolicy(
                "foreground-refresh-v1",
                AbuseOperation.FOREGROUND_REFRESH,
                (
                    bucket(AbuseDimension.SESSION, capacity=12, refill=6, period=60, cost=1),
                    bucket(AbuseDimension.IP_PREFIX, capacity=40, refill=20, period=60, cost=1),
                    bucket(
                        AbuseDimension.PROVIDER_ISSUER,
                        capacity=300,
                        refill=150,
                        period=60,
                        cost=1,
                    ),
                    bucket(
                        AbuseDimension.GLOBAL_OPERATION,
                        capacity=600,
                        refill=300,
                        period=60,
                        cost=1,
                    ),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "background-refresh-v1",
                AbuseOperation.BACKGROUND_REFRESH,
                (
                    bucket(AbuseDimension.SESSION, capacity=4, refill=2, period=60, cost=1),
                    bucket(
                        AbuseDimension.PROVIDER_ISSUER,
                        capacity=300,
                        refill=150,
                        period=60,
                        cost=1,
                    ),
                    bucket(
                        AbuseDimension.PROVIDER_CLIENT,
                        capacity=300,
                        refill=150,
                        period=60,
                        cost=1,
                    ),
                    bucket(
                        AbuseDimension.GLOBAL_OPERATION,
                        capacity=600,
                        refill=300,
                        period=60,
                        cost=1,
                    ),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "logout-v1",
                AbuseOperation.LOGOUT,
                (
                    bucket(AbuseDimension.SESSION, capacity=10, refill=5, period=60, cost=1),
                    bucket(AbuseDimension.IP_PREFIX, capacity=40, refill=20, period=60, cost=1),
                    bucket(
                        AbuseDimension.GLOBAL_OPERATION,
                        capacity=600,
                        refill=300,
                        period=60,
                        cost=1,
                    ),
                ),
                AbuseFailureMode.ALWAYS_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "provider-end-session-v1",
                AbuseOperation.PROVIDER_END_SESSION,
                (
                    bucket(
                        AbuseDimension.PROVIDER_ISSUER,
                        capacity=100,
                        refill=50,
                        period=60,
                        cost=1,
                    ),
                    bucket(AbuseDimension.SESSION, capacity=4, refill=2, period=60, cost=1),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "access-revocation-v1",
                AbuseOperation.ACCESS_TOKEN_REVOCATION,
                (
                    bucket(
                        AbuseDimension.PROVIDER_ISSUER,
                        capacity=150,
                        refill=75,
                        period=60,
                        cost=1,
                    ),
                    bucket(AbuseDimension.SESSION, capacity=4, refill=2, period=60, cost=1),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "refresh-revocation-v1",
                AbuseOperation.REFRESH_TOKEN_REVOCATION,
                (
                    bucket(
                        AbuseDimension.PROVIDER_ISSUER,
                        capacity=150,
                        refill=75,
                        period=60,
                        cost=1,
                    ),
                    bucket(AbuseDimension.SESSION, capacity=4, refill=2, period=60, cost=1),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                standard_penalty,
            ),
            OperationPolicy(
                "backchannel-logout-v1",
                AbuseOperation.BACKCHANNEL_LOGOUT,
                (
                    bucket(
                        AbuseDimension.PROVIDER_ISSUER,
                        capacity=200,
                        refill=100,
                        period=60,
                        cost=2,
                    ),
                    bucket(
                        AbuseDimension.BACKCHANNEL_JTI, capacity=2, refill=1, period=300, cost=1
                    ),
                    bucket(
                        AbuseDimension.GLOBAL_OPERATION,
                        capacity=400,
                        refill=200,
                        period=60,
                        cost=2,
                    ),
                ),
                AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
                conservative_penalty,
            ),
        )
        return cls(policies)
