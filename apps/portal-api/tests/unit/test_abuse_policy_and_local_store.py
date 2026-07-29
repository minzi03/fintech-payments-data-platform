"""Policy validation and bounded degraded-limiter tests."""

from __future__ import annotations

import pytest
from portal_api.abuse.local_store import BoundedLocalAbuseStore
from portal_api.abuse.models import (
    AbuseBucketRequest,
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationRequest,
    AbuseFailureMode,
    AbuseOperation,
)
from portal_api.abuse.policy import (
    AbusePolicyRegistry,
    BucketPolicy,
    DimensionPolicy,
    OperationPolicy,
    PenaltyPolicy,
)


def _bucket(
    key: str,
    dimension: AbuseDimension,
    *,
    capacity: int = 1,
    ttl_ms: int = 60_000,
) -> AbuseBucketRequest:
    return AbuseBucketRequest(
        key=key,
        dimension=dimension,
        capacity=capacity,
        refill_tokens=1,
        refill_period_ms=60_000,
        request_cost=1,
        state_ttl_ms=ttl_ms,
        penalty_base_ms=1_000,
        penalty_max_level=3,
        penalty_decay_ms=60_000,
    )


def _request(*buckets: AbuseBucketRequest) -> AbuseEvaluationRequest:
    return AbuseEvaluationRequest(
        operation=AbuseOperation.LOGIN_INITIATION,
        policy_name="test-policy",
        policy_version="test-v1",
        buckets=buckets,
    )


def test_default_registry_has_a_policy_for_every_operation() -> None:
    registry = AbusePolicyRegistry.development_defaults()

    assert all(registry.get(operation).operation is operation for operation in AbuseOperation)
    background = registry.get(AbuseOperation.BACKGROUND_REFRESH)
    assert AbuseDimension.IP_PREFIX not in {
        dimension.dimension for dimension in background.dimensions
    }


@pytest.mark.parametrize(
    "policy",
    [
        BucketPolicy(0, 1, 1, 1, 10),
        BucketPolicy(1, 0, 1, 1, 10),
        BucketPolicy(1, 1, 0.01, 1, 10),
        BucketPolicy(1, 1, 1, 1, 100_000),
    ],
)
def test_bucket_policy_rejects_unbounded_or_invalid_values(policy: BucketPolicy) -> None:
    with pytest.raises(ValueError):
        policy.validate()


def test_registry_rejects_duplicate_policy_identity() -> None:
    dimension = DimensionPolicy(
        AbuseDimension.GLOBAL_OPERATION,
        BucketPolicy(10, 1, 60, 1, 600),
    )
    first = OperationPolicy(
        "duplicate-v1",
        AbuseOperation.LOGIN_INITIATION,
        (dimension,),
        AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
        PenaltyPolicy(),
    )
    second = OperationPolicy(
        "duplicate-v1",
        AbuseOperation.OIDC_CALLBACK,
        (dimension,),
        AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
        PenaltyPolicy(),
    )

    with pytest.raises(ValueError, match="Duplicate abuse policy name"):
        AbusePolicyRegistry((first, second))


@pytest.mark.asyncio
async def test_local_store_escalates_bounded_temporary_penalty() -> None:
    store = BoundedLocalAbuseStore(maximum_keys=100)
    request = _request(
        _bucket(
            "portal:abuse:{portal-abuse}:test:login:ip:one",
            AbuseDimension.IP_PREFIX,
        )
    )

    assert (await store.evaluate(request)).decision is AbuseDecision.ALLOW
    assert (await store.evaluate(request)).decision is AbuseDecision.THROTTLE
    blocked = await store.evaluate(request)

    assert blocked.decision is AbuseDecision.TEMPORARILY_BLOCK
    assert 1 <= blocked.retry_after_seconds <= 3_600


@pytest.mark.asyncio
async def test_multi_bucket_rejection_does_not_partially_consume_allowing_bucket() -> None:
    store = BoundedLocalAbuseStore(maximum_keys=100)
    session = _bucket(
        "portal:abuse:{portal-abuse}:test:login:session:one",
        AbuseDimension.SESSION,
        capacity=2,
    )
    global_bucket = _bucket(
        "portal:abuse:{portal-abuse}:test:login:global:one",
        AbuseDimension.GLOBAL_OPERATION,
        capacity=1,
    )

    assert (await store.evaluate(_request(session, global_bucket))).decision is AbuseDecision.ALLOW
    assert (
        await store.evaluate(_request(session, global_bucket))
    ).decision is AbuseDecision.THROTTLE
    assert (await store.evaluate(_request(session))).decision is AbuseDecision.ALLOW


@pytest.mark.asyncio
async def test_local_concurrency_lease_cannot_release_another_owner() -> None:
    from portal_api.abuse.models import AbuseConcurrencyRequest

    store = BoundedLocalAbuseStore(maximum_keys=100)
    first = await store.acquire_concurrency(
        AbuseConcurrencyRequest(
            key="portal:abuse:{portal-abuse}:concurrency",
            operation=AbuseOperation.BACKGROUND_REFRESH,
            limit=1,
            lease_ms=30_000,
            owner_token="owner-one",
        )
    )
    assert first is not None
    denied = await store.acquire_concurrency(
        AbuseConcurrencyRequest(
            key=first.key,
            operation=AbuseOperation.BACKGROUND_REFRESH,
            limit=1,
            lease_ms=30_000,
            owner_token="owner-two",
        )
    )
    assert denied is None
    await store.release_concurrency(first)
