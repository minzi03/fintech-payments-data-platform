"""Validate distributed abuse enforcement against a disposable real Redis database."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import tracemalloc
from collections import Counter
from collections.abc import Awaitable
from dataclasses import asdict, dataclass
from typing import Any, cast

from portal_api.abuse.models import (
    AbuseBucketRequest,
    AbuseConcurrencyRequest,
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationRequest,
    AbuseOperation,
)
from portal_api.abuse.redis_store import RedisAbuseStore
from redis.asyncio import Redis


@dataclass(frozen=True)
class LoadReport:
    total_operations: int
    workers: int
    allowed: int
    throttled: int
    blocked: int
    invariant_violations: int
    throughput_per_second: float
    p50_ms: float
    p95_ms: float
    p99_ms: float
    provider_max_concurrency: int
    provider_throttled: int
    redis_keys_before_ttl: int
    redis_keys_after_ttl: int
    peak_memory_bytes: int


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * quantile)))
    return ordered[index]


def _request(*, capacity: int, ttl_ms: int) -> AbuseEvaluationRequest:
    dimensions = (
        AbuseDimension.IP_PREFIX,
        AbuseDimension.SESSION,
        AbuseDimension.PROVIDER_ISSUER,
        AbuseDimension.GLOBAL_OPERATION,
    )
    return AbuseEvaluationRequest(
        operation=AbuseOperation.LOGIN_INITIATION,
        policy_name="load-v1",
        policy_version="load-v1",
        buckets=tuple(
            AbuseBucketRequest(
                key=f"portal:abuse:{{portal-abuse}}:load:{dimension.value}:fingerprint",
                dimension=dimension,
                capacity=capacity,
                refill_tokens=1,
                refill_period_ms=3_600_000,
                request_cost=1,
                state_ttl_ms=ttl_ms,
                penalty_base_ms=1_000,
                penalty_max_level=3,
                penalty_decay_ms=60_000,
            )
            for dimension in dimensions
        ),
    )


async def _run(
    *,
    redis_url: str,
    operations: int,
    workers: int,
    capacity: int,
    ttl_ms: int,
) -> LoadReport:
    client = Redis.from_url(redis_url, decode_responses=False)
    await cast(Awaitable[Any], client.flushdb())
    stores = tuple(
        RedisAbuseStore(
            url=redis_url,
            connect_timeout_seconds=0.5,
            operation_timeout_seconds=2,
            maximum_connections=max(20, workers),
        )
        for _ in range(max(2, min(workers, 8)))
    )
    request = _request(capacity=capacity, ttl_ms=ttl_ms)
    queue: asyncio.Queue[int] = asyncio.Queue()
    for index in range(operations):
        queue.put_nowait(index)
    outcomes: Counter[AbuseDecision] = Counter()
    latencies: list[float] = []

    async def consume(worker: int) -> None:
        store = stores[worker % len(stores)]
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            started = time.perf_counter()
            result = await store.evaluate(request)
            latencies.append((time.perf_counter() - started) * 1000)
            outcomes[result.decision] += 1
            queue.task_done()

    tracemalloc.start()
    started = time.perf_counter()
    await asyncio.gather(*(consume(worker) for worker in range(workers)))
    duration = time.perf_counter() - started

    provider_active = 0
    provider_peak = 0
    provider_throttled = 0
    concurrency_lock = asyncio.Lock()

    async def provider_attempt(index: int) -> None:
        nonlocal provider_active, provider_peak, provider_throttled
        store = stores[index % len(stores)]
        lease = await store.acquire_concurrency(
            AbuseConcurrencyRequest(
                key="portal:abuse:{portal-abuse}:load:provider-concurrency",
                operation=AbuseOperation.BACKGROUND_REFRESH,
                limit=12,
                lease_ms=2_000,
                owner_token=f"load-owner-{index}",
            )
        )
        if lease is None:
            provider_throttled += 1
            return
        async with concurrency_lock:
            provider_active += 1
            provider_peak = max(provider_peak, provider_active)
        await asyncio.sleep(0.05)
        async with concurrency_lock:
            provider_active -= 1
        await store.release_concurrency(lease)

    await asyncio.gather(*(provider_attempt(index) for index in range(100)))
    keys_before = len(await cast(Awaitable[list[bytes]], client.keys("*")))
    await asyncio.sleep(ttl_ms / 1000 + 0.5)
    keys_after = len(await cast(Awaitable[list[bytes]], client.keys("*")))
    _, peak_memory = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    allowed = outcomes[AbuseDecision.ALLOW]
    report = LoadReport(
        total_operations=operations,
        workers=workers,
        allowed=allowed,
        throttled=outcomes[AbuseDecision.THROTTLE],
        blocked=outcomes[AbuseDecision.TEMPORARILY_BLOCK],
        invariant_violations=abs(allowed - capacity),
        throughput_per_second=operations / max(duration, 0.000001),
        p50_ms=statistics.median(latencies),
        p95_ms=_percentile(latencies, 0.95),
        p99_ms=_percentile(latencies, 0.99),
        provider_max_concurrency=provider_peak,
        provider_throttled=provider_throttled,
        redis_keys_before_ttl=keys_before,
        redis_keys_after_ttl=keys_after,
        peak_memory_bytes=peak_memory,
    )
    for store in stores:
        await store.close()
    await client.aclose()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--operations", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=100)
    parser.add_argument("--capacity", type=int, default=1_000)
    parser.add_argument("--ttl-ms", type=int, default=2_000)
    arguments = parser.parse_args()
    if arguments.operations < arguments.capacity or arguments.workers < 2:
        parser.error("operations must cover capacity and at least two workers are required")
    redis_url = os.getenv("PORTAL_TEST_REDIS_URL", "redis://127.0.0.1:56379/14")
    report = asyncio.run(
        _run(
            redis_url=redis_url,
            operations=arguments.operations,
            workers=arguments.workers,
            capacity=arguments.capacity,
            ttl_ms=arguments.ttl_ms,
        )
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    if (
        report.invariant_violations
        or report.provider_max_concurrency > 12
        or report.redis_keys_after_ttl
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
