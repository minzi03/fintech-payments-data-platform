"""Public 429 semantics stay generic and preserve correlation."""

from __future__ import annotations

from fastapi.testclient import TestClient
from portal_api.abuse.client_address import (
    ClientAddressSettings,
    ForwardedHeaderMode,
    TrustedClientAddressResolver,
)
from portal_api.abuse.local_store import BoundedLocalAbuseStore
from portal_api.abuse.middleware import AbuseContextMiddleware
from portal_api.abuse.models import AbuseDimension, AbuseFailureMode, AbuseOperation
from portal_api.abuse.policy import (
    AbusePolicyRegistry,
    BucketPolicy,
    DimensionPolicy,
    OperationPolicy,
    PenaltyPolicy,
)
from portal_api.abuse.service import AbuseProtectionService
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app


def test_login_throttle_returns_generic_non_cacheable_problem_details() -> None:
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        trusted_hosts="testserver",
        allowed_origins="http://portal.test",
    )
    resolver = TrustedClientAddressResolver(
        ClientAddressSettings(
            trusted_proxy_cidrs=(),
            forwarded_header_mode=ForwardedHeaderMode.DIRECT,
            max_forwarded_hops=5,
            ipv4_prefix_length=24,
            ipv6_prefix_length=64,
            fingerprint_key=bytes(range(32)),
        )
    )
    policy = OperationPolicy(
        name="http-login-v1",
        operation=AbuseOperation.LOGIN_INITIATION,
        dimensions=(
            DimensionPolicy(
                AbuseDimension.GLOBAL_OPERATION,
                BucketPolicy(
                    capacity=1,
                    refill_tokens=1,
                    refill_period_seconds=60,
                    request_cost=1,
                    state_ttl_seconds=60,
                ),
            ),
        ),
        backend_failure_mode=AbuseFailureMode.LOCAL_FALLBACK_ALLOW,
        penalty=PenaltyPolicy(),
    )
    store = BoundedLocalAbuseStore(maximum_keys=100)
    service = AbuseProtectionService(
        settings=settings,
        resolver=resolver,
        policies=AbusePolicyRegistry((policy,)),
        distributed_store=store,
        local_fallback=BoundedLocalAbuseStore(maximum_keys=100),
    )
    app = create_app(settings=settings)
    app.state.abuse_protection_service = service
    app.add_middleware(AbuseContextMiddleware, resolver=resolver)

    with TestClient(app) as client:
        first = client.post(
            "/v1/auth/login",
            json={"intent_token": "not-a-valid-intent", "return_to": "/"},
            headers={"X-Request-ID": "first-request", "Origin": "http://portal.test"},
        )
        second = client.post(
            "/v1/auth/login",
            json={"intent_token": "not-a-valid-intent", "return_to": "/"},
            headers={"X-Request-ID": "second-request", "Origin": "http://portal.test"},
        )

    assert first.status_code == 503
    assert second.status_code == 429
    assert second.headers["Retry-After"].isdigit()
    assert second.headers["Cache-Control"] == "no-store"
    assert second.headers["Pragma"] == "no-cache"
    payload = second.json()
    assert payload["error_code"] == "RATE_LIMITED"
    assert payload["detail"] == "The request cannot be accepted at this time."
    assert payload["correlation_id"]
    assert "bucket" not in second.text.casefold()
    assert "redis" not in second.text.casefold()
