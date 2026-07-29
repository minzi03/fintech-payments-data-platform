"""Bounded process-local limiter used only during explicit degraded operation."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
from math import ceil
from time import time

from portal_api.abuse.models import (
    AbuseBackendStatus,
    AbuseBucketRequest,
    AbuseConcurrencyLease,
    AbuseConcurrencyRequest,
    AbuseDecision,
    AbuseEvaluationRequest,
    AbuseEvaluationResult,
    PenaltyLevel,
)


@dataclass(slots=True)
class _BucketState:
    tokens: float
    updated_ms: int
    violations: int
    penalty: int
    block_until_ms: int
    last_violation_ms: int
    expires_at_ms: int


class BoundedLocalAbuseStore:
    """Async-safe LRU token buckets with TTL and a hard key ceiling."""

    def __init__(self, *, maximum_keys: int = 10_000) -> None:
        if not 1 <= maximum_keys <= 100_000:
            raise ValueError("Local abuse fallback key limit must be bounded")
        self._maximum_keys = maximum_keys
        self._states: OrderedDict[str, _BucketState] = OrderedDict()
        self._leases: dict[str, dict[str, int]] = {}
        self._lock = asyncio.Lock()

    async def evaluate(self, request: AbuseEvaluationRequest) -> AbuseEvaluationResult:
        now_ms = int(time() * 1000)
        async with self._lock:
            self._evict(now_ms)
            candidates: list[tuple[AbuseBucketRequest, _BucketState, float, bool]] = []
            for bucket in request.buckets:
                state = self._states.get(bucket.key)
                if state is None:
                    state = _BucketState(
                        tokens=float(bucket.capacity),
                        updated_ms=now_ms,
                        violations=0,
                        penalty=0,
                        block_until_ms=0,
                        last_violation_ms=0,
                        expires_at_ms=now_ms + bucket.state_ttl_ms,
                    )
                self._decay(state, now_ms, bucket.penalty_decay_ms)
                elapsed = max(0, now_ms - state.updated_ms)
                available = min(
                    float(bucket.capacity),
                    state.tokens + elapsed * bucket.refill_tokens / max(1, bucket.refill_period_ms),
                )
                denied = state.block_until_ms > now_ms or available < bucket.request_cost
                candidates.append((bucket, state, available, denied))

            limiting = next((item for item in candidates if item[3]), None)
            if limiting is None:
                remaining = 2**31 - 1
                for bucket, state, available, _ in candidates:
                    state.tokens = available - bucket.request_cost
                    state.updated_ms = now_ms
                    state.expires_at_ms = now_ms + bucket.state_ttl_ms
                    self._put(bucket.key, state)
                    remaining = min(remaining, int(state.tokens))
                return AbuseEvaluationResult(
                    decision=AbuseDecision.ALLOW,
                    policy_name=request.policy_name,
                    policy_version=request.policy_version,
                    limiting_dimension=None,
                    retry_after_seconds=0,
                    remaining=max(0, remaining),
                    backend_status=AbuseBackendStatus.DEGRADED,
                    penalty_level=PenaltyLevel.NORMAL,
                )

            bucket, state, available, _ = limiting
            if state.block_until_ms <= now_ms:
                state.violations += 1
                state.penalty = max(1, state.penalty)
                if state.violations >= 2:
                    state.penalty = min(bucket.penalty_max_level, state.penalty + 1)
                    multiplier = 2 ** max(0, state.penalty - 2)
                    state.block_until_ms = now_ms + bucket.penalty_base_ms * multiplier
                state.last_violation_ms = now_ms
            state.tokens = available
            state.updated_ms = now_ms
            state.expires_at_ms = now_ms + bucket.state_ttl_ms
            self._put(bucket.key, state)
            blocked = state.block_until_ms > now_ms
            if blocked:
                retry_ms = state.block_until_ms - now_ms
            else:
                missing = max(0.0, bucket.request_cost - available)
                retry_ms = ceil(missing * bucket.refill_period_ms / bucket.refill_tokens)
            return AbuseEvaluationResult(
                decision=(AbuseDecision.TEMPORARILY_BLOCK if blocked else AbuseDecision.THROTTLE),
                policy_name=request.policy_name,
                policy_version=request.policy_version,
                limiting_dimension=bucket.dimension,
                retry_after_seconds=max(1, ceil(retry_ms / 1000)),
                remaining=max(0, int(available)),
                backend_status=AbuseBackendStatus.DEGRADED,
                penalty_level=self._penalty(state.penalty),
            )

    async def acquire_concurrency(
        self,
        request: AbuseConcurrencyRequest,
    ) -> AbuseConcurrencyLease | None:
        now_ms = int(time() * 1000)
        async with self._lock:
            leases = self._leases.setdefault(request.key, {})
            for owner, expires_at in tuple(leases.items()):
                if expires_at <= now_ms:
                    del leases[owner]
            if len(leases) >= request.limit:
                return None
            leases[request.owner_token] = now_ms + request.lease_ms
            return AbuseConcurrencyLease(
                key=request.key,
                owner_token=request.owner_token,
                expires_in_ms=request.lease_ms,
            )

    async def release_concurrency(self, lease: AbuseConcurrencyLease) -> None:
        async with self._lock:
            leases = self._leases.get(lease.key)
            if leases is None:
                return
            leases.pop(lease.owner_token, None)
            if not leases:
                self._leases.pop(lease.key, None)

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        async with self._lock:
            self._states.clear()
            self._leases.clear()

    def _put(self, key: str, state: _BucketState) -> None:
        self._states[key] = state
        self._states.move_to_end(key)
        while len(self._states) > self._maximum_keys:
            self._states.popitem(last=False)

    def _evict(self, now_ms: int) -> None:
        for key, state in tuple(self._states.items()):
            if state.expires_at_ms <= now_ms:
                del self._states[key]

    @staticmethod
    def _decay(state: _BucketState, now_ms: int, decay_ms: int) -> None:
        if state.last_violation_ms <= 0 or now_ms - state.last_violation_ms < decay_ms:
            return
        elapsed_periods = (now_ms - state.last_violation_ms) // decay_ms
        state.penalty = max(0, state.penalty - int(elapsed_periods))
        state.violations = 0 if state.penalty == 0 else max(0, state.violations - 1)
        if state.block_until_ms <= now_ms:
            state.block_until_ms = 0
        state.last_violation_ms = now_ms

    @staticmethod
    def _penalty(level: int) -> PenaltyLevel:
        return {
            0: PenaltyLevel.NORMAL,
            1: PenaltyLevel.THROTTLED,
            2: PenaltyLevel.TEMPORARILY_BLOCKED,
        }.get(level, PenaltyLevel.EXTENDED_BLOCK)
