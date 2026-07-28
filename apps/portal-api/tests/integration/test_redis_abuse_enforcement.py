"""Real-Redis atomicity, multi-instance, TTL, and script recovery tests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from typing import Any, cast

import pytest
from portal_api.abuse.models import (
    AbuseBucketRequest,
    AbuseConcurrencyRequest,
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationRequest,
    AbuseOperation,
)
from portal_api.abuse.ports import AbuseBackendTimeout, AbuseBackendUnavailable
from portal_api.abuse.redis_store import RedisAbuseStore
from redis.asyncio import Redis

REDIS_URL = "redis://127.0.0.1:56379/15"


def _store() -> RedisAbuseStore:
    return RedisAbuseStore(
        url=REDIS_URL,
        connect_timeout_seconds=0.5,
        operation_timeout_seconds=1,
        maximum_connections=100,
    )


def _request(key_suffix: str, *, capacity: int = 25, ttl_ms: int = 2_000):
    return AbuseEvaluationRequest(
        operation=AbuseOperation.LOGIN_INITIATION,
        policy_name="redis-test-v1",
        policy_version="test-v1",
        buckets=(
            AbuseBucketRequest(
                key=f"portal:abuse:{{portal-abuse}}:test:{key_suffix}",
                dimension=AbuseDimension.IP_PREFIX,
                capacity=capacity,
                refill_tokens=1,
                refill_period_ms=60_000,
                request_cost=1,
                state_ttl_ms=ttl_ms,
                penalty_base_ms=1_000,
                penalty_max_level=3,
                penalty_decay_ms=60_000,
            ),
        ),
    )


async def _flush() -> Redis:
    client = Redis.from_url(REDIS_URL, decode_responses=False)
    await cast(Awaitable[Any], client.flushdb())
    return client


@pytest.mark.integration
@pytest.mark.asyncio
async def test_atomic_quota_is_shared_across_two_application_stores() -> None:
    client = await _flush()
    first = _store()
    second = _store()
    try:
        request = _request("shared-burst", capacity=25)
        results = await asyncio.gather(
            *((first if index % 2 else second).evaluate(request) for index in range(100))
        )
        allowed = sum(item.decision is AbuseDecision.ALLOW for item in results)

        assert allowed == 25
        assert all(
            item.decision
            in {
                AbuseDecision.ALLOW,
                AbuseDecision.THROTTLE,
                AbuseDecision.TEMPORARILY_BLOCK,
            }
            for item in results
        )
    finally:
        await first.close()
        await second.close()
        await client.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_noscript_reload_ttl_cleanup_and_safe_key_material() -> None:
    client = await _flush()
    store = _store()
    try:
        request = _request("fingerprint-not-raw-identity", capacity=2, ttl_ms=300)
        assert (await store.evaluate(request)).decision is AbuseDecision.ALLOW
        await cast(Awaitable[Any], client.script_flush())
        assert (await store.evaluate(request)).decision is AbuseDecision.ALLOW

        keys = await cast(Awaitable[list[bytes]], client.keys("*"))
        assert keys
        assert all(b"@" not in key and b"192.0.2." not in key for key in keys)
        await asyncio.sleep(0.45)
        assert await cast(Awaitable[list[bytes]], client.keys("*")) == []
    finally:
        await store.close()
        await client.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_distributed_concurrency_lease_is_owner_safe() -> None:
    client = await _flush()
    first = _store()
    second = _store()
    try:
        request = AbuseConcurrencyRequest(
            key="portal:abuse:{portal-abuse}:test:concurrency",
            operation=AbuseOperation.BACKGROUND_REFRESH,
            limit=1,
            lease_ms=2_000,
            owner_token="first-owner",
        )
        lease = await first.acquire_concurrency(request)
        assert lease is not None
        denied = await second.acquire_concurrency(
            AbuseConcurrencyRequest(
                key=request.key,
                operation=request.operation,
                limit=1,
                lease_ms=2_000,
                owner_token="second-owner",
            )
        )
        assert denied is None
        await second.release_concurrency(
            type(lease)(lease.key, "not-the-owner", lease.expires_in_ms)
        )
        assert (
            await second.acquire_concurrency(
                AbuseConcurrencyRequest(
                    key=request.key,
                    operation=request.operation,
                    limit=1,
                    lease_ms=2_000,
                    owner_token="third-owner",
                )
            )
            is None
        )
        await first.release_concurrency(lease)
    finally:
        await first.close()
        await second.close()
        await client.aclose()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_connection_loss_is_bounded_and_classified() -> None:
    store = RedisAbuseStore(
        url="redis://127.0.0.1:1/0",
        connect_timeout_seconds=0.1,
        operation_timeout_seconds=0.1,
        maximum_connections=2,
    )
    try:
        with pytest.raises((AbuseBackendTimeout, AbuseBackendUnavailable)):
            await store.evaluate(_request("unreachable"))
    finally:
        await store.close()
