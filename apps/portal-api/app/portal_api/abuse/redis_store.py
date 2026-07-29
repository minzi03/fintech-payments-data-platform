"""Atomic Redis enforcement and leased provider concurrency."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable
from math import ceil
from typing import Any, cast

from redis.asyncio import Redis
from redis.exceptions import (
    ConnectionError as RedisConnectionError,
)
from redis.exceptions import (
    NoScriptError,
    RedisError,
)
from redis.exceptions import (
    TimeoutError as RedisTimeoutError,
)

from portal_api.abuse.models import (
    AbuseBackendStatus,
    AbuseConcurrencyLease,
    AbuseConcurrencyRequest,
    AbuseDecision,
    AbuseEvaluationRequest,
    AbuseEvaluationResult,
    PenaltyLevel,
)
from portal_api.abuse.ports import AbuseBackendTimeout, AbuseBackendUnavailable

_SAFE_KEY = re.compile(r"^[A-Za-z0-9:{}_.-]{1,220}$")

_TOKEN_BUCKET_SCRIPT = r"""
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
local count = tonumber(ARGV[1])
local states = {}
local overall = 1
local limiting = 0
local retry_after = 0
local lowest_remaining = 2147483647
local highest_penalty = 0

for index = 1, count do
  local offset = 2 + (index - 1) * 8
  local capacity = tonumber(ARGV[offset])
  local refill = tonumber(ARGV[offset + 1])
  local period = tonumber(ARGV[offset + 2])
  local cost = tonumber(ARGV[offset + 3])
  local ttl = tonumber(ARGV[offset + 4])
  local penalty_base = tonumber(ARGV[offset + 5])
  local penalty_max = tonumber(ARGV[offset + 6])
  local penalty_decay = tonumber(ARGV[offset + 7])
  local values = redis.call(
    'HMGET', KEYS[index],
    'tokens', 'updated', 'violations', 'penalty', 'block_until', 'last_violation'
  )
  local tokens = tonumber(values[1]) or capacity
  local updated = tonumber(values[2]) or now
  local violations = tonumber(values[3]) or 0
  local penalty = tonumber(values[4]) or 0
  local block_until = tonumber(values[5]) or 0
  local last_violation = tonumber(values[6]) or 0
  if updated > now then updated = now end
  if last_violation > 0 and now - last_violation >= penalty_decay then
    local periods = math.floor((now - last_violation) / penalty_decay)
    penalty = math.max(0, penalty - periods)
    if penalty == 0 then violations = 0 else violations = math.max(0, violations - 1) end
    last_violation = now
    if block_until <= now then block_until = 0 end
  end
  tokens = math.min(capacity, tokens + ((now - updated) * refill / period))
  local denied = block_until > now or tokens < cost
  local bucket_retry = 0
  local decision = 1
  if denied then
    if block_until > now then
      decision = 3
      bucket_retry = block_until - now
    else
      decision = 2
      bucket_retry = math.ceil((cost - tokens) * period / refill)
    end
    if decision > overall or (decision == overall and bucket_retry > retry_after) then
      overall = decision
      limiting = index
      retry_after = bucket_retry
    end
  end
  states[index] = {
    tokens=tokens, updated=now, violations=violations, penalty=penalty,
    block_until=block_until, last_violation=last_violation, denied=denied,
    capacity=capacity, cost=cost, ttl=ttl, penalty_base=penalty_base,
    penalty_max=penalty_max
  }
  lowest_remaining = math.min(lowest_remaining, math.floor(tokens))
end

if overall == 1 then
  for index = 1, count do
    local state = states[index]
    state.tokens = state.tokens - state.cost
    redis.call(
      'HSET', KEYS[index],
      'tokens', state.tokens, 'updated', state.updated,
      'violations', state.violations, 'penalty', state.penalty,
      'block_until', state.block_until, 'last_violation', state.last_violation
    )
    redis.call('PEXPIRE', KEYS[index], state.ttl)
    lowest_remaining = math.min(lowest_remaining, math.floor(state.tokens))
  end
else
  for index = 1, count do
    local state = states[index]
    if state.denied and state.block_until <= now then
      state.violations = state.violations + 1
      state.penalty = math.max(1, state.penalty)
      if state.violations >= 2 then
        state.penalty = math.min(state.penalty_max, state.penalty + 1)
        local multiplier = math.pow(2, math.max(0, state.penalty - 2))
        state.block_until = now + state.penalty_base * multiplier
        if state.penalty >= 2 then
          overall = 3
          if index == limiting then retry_after = state.block_until - now end
        end
      end
      state.last_violation = now
    end
    highest_penalty = math.max(highest_penalty, state.penalty)
    redis.call(
      'HSET', KEYS[index],
      'tokens', state.tokens, 'updated', state.updated,
      'violations', state.violations, 'penalty', state.penalty,
      'block_until', state.block_until, 'last_violation', state.last_violation
    )
    redis.call('PEXPIRE', KEYS[index], state.ttl)
  end
end

return {overall, math.max(0, retry_after), limiting, math.max(0, lowest_remaining), highest_penalty}
"""

_ACQUIRE_SCRIPT = r"""
local now_parts = redis.call('TIME')
local now = tonumber(now_parts[1]) * 1000 + math.floor(tonumber(now_parts[2]) / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[1]) then
  local earliest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  local retry = 1
  if #earliest == 2 then retry = math.max(1, tonumber(earliest[2]) - now) end
  return {0, retry}
end
local expires = now + tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], 'NX', expires, ARGV[3])
redis.call('PEXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
return {1, tonumber(ARGV[2])}
"""

_RELEASE_SCRIPT = r"""
return redis.call('ZREM', KEYS[1], ARGV[1])
"""


class RedisAbuseStore:
    """One-round-trip multi-bucket enforcement using Redis server time."""

    def __init__(
        self,
        *,
        url: str,
        connect_timeout_seconds: float,
        operation_timeout_seconds: float,
        maximum_connections: int,
    ) -> None:
        self._client: Redis = Redis.from_url(
            url,
            decode_responses=False,
            socket_connect_timeout=connect_timeout_seconds,
            socket_timeout=operation_timeout_seconds,
            max_connections=maximum_connections,
            health_check_interval=15,
        )
        self._operation_timeout = operation_timeout_seconds
        self._script_sha: str | None = None
        self._acquire_sha: str | None = None
        self._release_sha: str | None = None
        self._script_lock = asyncio.Lock()

    async def evaluate(self, request: AbuseEvaluationRequest) -> AbuseEvaluationResult:
        if not request.buckets:
            raise ValueError("Redis abuse evaluation requires at least one bucket")
        keys = [bucket.key for bucket in request.buckets]
        self._validate_keys(keys)
        arguments: list[str | int] = [len(request.buckets)]
        for bucket in request.buckets:
            arguments.extend(
                (
                    bucket.capacity,
                    bucket.refill_tokens,
                    bucket.refill_period_ms,
                    bucket.request_cost,
                    bucket.state_ttl_ms,
                    bucket.penalty_base_ms,
                    bucket.penalty_max_level,
                    bucket.penalty_decay_ms,
                )
            )
        raw = await self._execute_script(
            "token",
            _TOKEN_BUCKET_SCRIPT,
            keys,
            arguments,
        )
        if not isinstance(raw, (list, tuple)) or len(raw) != 5:
            raise AbuseBackendUnavailable("Redis returned an invalid abuse decision")
        decision_code, retry_ms, limiting_index, remaining, penalty = (int(value) for value in raw)
        if decision_code not in {1, 2, 3}:
            raise AbuseBackendUnavailable("Redis returned an unknown abuse decision")
        limiting_dimension = None
        if limiting_index:
            if not 1 <= limiting_index <= len(request.buckets):
                raise AbuseBackendUnavailable("Redis returned an invalid limiting dimension")
            limiting_dimension = request.buckets[limiting_index - 1].dimension
        return AbuseEvaluationResult(
            decision={
                1: AbuseDecision.ALLOW,
                2: AbuseDecision.THROTTLE,
                3: AbuseDecision.TEMPORARILY_BLOCK,
            }[decision_code],
            policy_name=request.policy_name,
            policy_version=request.policy_version,
            limiting_dimension=limiting_dimension,
            retry_after_seconds=max(0, ceil(retry_ms / 1000)),
            remaining=max(0, remaining),
            backend_status=AbuseBackendStatus.UP,
            penalty_level={
                0: PenaltyLevel.NORMAL,
                1: PenaltyLevel.THROTTLED,
                2: PenaltyLevel.TEMPORARILY_BLOCKED,
            }.get(penalty, PenaltyLevel.EXTENDED_BLOCK),
        )

    async def acquire_concurrency(
        self,
        request: AbuseConcurrencyRequest,
    ) -> AbuseConcurrencyLease | None:
        self._validate_keys([request.key])
        raw = await self._execute_script(
            "acquire",
            _ACQUIRE_SCRIPT,
            [request.key],
            [request.limit, request.lease_ms, request.owner_token],
        )
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise AbuseBackendUnavailable("Redis returned an invalid concurrency decision")
        if int(raw[0]) != 1:
            return None
        return AbuseConcurrencyLease(
            key=request.key,
            owner_token=request.owner_token,
            expires_in_ms=int(raw[1]),
        )

    async def release_concurrency(self, lease: AbuseConcurrencyLease) -> None:
        self._validate_keys([lease.key])
        await self._execute_script(
            "release",
            _RELEASE_SCRIPT,
            [lease.key],
            [lease.owner_token],
        )

    async def ping(self) -> bool:
        try:
            async with asyncio.timeout(self._operation_timeout):
                return bool(await cast(Awaitable[bool], self._client.ping()))
        except TimeoutError as error:
            raise AbuseBackendTimeout("Redis health check timed out") from error
        except (RedisConnectionError, RedisTimeoutError, RedisError) as error:
            raise AbuseBackendUnavailable("Redis health check failed") from error

    async def close(self) -> None:
        await self._client.aclose()

    async def _execute_script(
        self,
        kind: str,
        script: str,
        keys: list[str],
        arguments: list[str | int],
    ) -> Any:
        try:
            async with asyncio.timeout(self._operation_timeout):
                sha = await self._sha(kind, script)
                try:
                    return await cast(
                        Awaitable[Any],
                        self._client.evalsha(sha, len(keys), *keys, *arguments),
                    )
                except NoScriptError:
                    return await cast(
                        Awaitable[Any],
                        self._client.eval(script, len(keys), *keys, *arguments),
                    )
        except TimeoutError as error:
            raise AbuseBackendTimeout("Redis abuse operation timed out") from error
        except RedisTimeoutError as error:
            raise AbuseBackendTimeout("Redis abuse operation timed out") from error
        except (RedisConnectionError, RedisError) as error:
            raise AbuseBackendUnavailable("Redis abuse operation failed") from error

    async def _sha(self, kind: str, script: str) -> str:
        attribute = {
            "token": "_script_sha",
            "acquire": "_acquire_sha",
            "release": "_release_sha",
        }[kind]
        existing = getattr(self, attribute)
        if existing is not None:
            return str(existing)
        async with self._script_lock:
            existing = getattr(self, attribute)
            if existing is None:
                existing = await cast(Awaitable[str], self._client.script_load(script))
                setattr(self, attribute, existing)
            return str(existing)

    @staticmethod
    def _validate_keys(keys: list[str]) -> None:
        if any(_SAFE_KEY.fullmatch(key) is None or "{portal-abuse}" not in key for key in keys):
            raise ValueError("Redis abuse keys must be bounded and share the portal hash tag")
