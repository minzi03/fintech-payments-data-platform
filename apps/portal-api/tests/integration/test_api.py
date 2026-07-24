"""Portal API foundation integration tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi import FastAPI
from fastapi.testclient import TestClient
from portal_api.adapters.models import (
    AdapterHealthResult,
    AdapterIdentity,
    DependencyStatus,
)
from portal_api.adapters.registry import AdapterRegistry
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.main import create_app


class UnavailableOptionalAdapter:
    identity = AdapterIdentity(
        adapter_id="optional-test",
        display_name="Optional test dependency",
        dependency_type="test",
        required=False,
        version="test-v1",
    )

    async def check_health(self) -> AdapterHealthResult:
        return AdapterHealthResult(
            identity=self.identity,
            status=DependencyStatus.UNAVAILABLE,
            observed_at=datetime.now(UTC),
            reason="Safe test reason.",
        )


class SlowRequiredAdapter:
    identity = AdapterIdentity(
        adapter_id="required-slow-test",
        display_name="Required slow dependency",
        dependency_type="test",
        required=True,
        version="test-v1",
    )

    async def check_health(self) -> AdapterHealthResult:
        await asyncio.sleep(0.1)
        return AdapterHealthResult(
            identity=self.identity,
            status=DependencyStatus.UP,
            observed_at=datetime.now(UTC),
        )


def test_liveness_is_safe_and_correlation_aware(client: TestClient) -> None:
    response = client.get("/health/live", headers={"X-Correlation-ID": "e2e-portal-1"})

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] == "e2e-portal-1"
    assert response.json() == {
        "status": "UP",
        "service": "portal-api",
        "version": "0.1.0-test",
        "build_sha": "test-sha",
        "time": response.json()["time"],
        "correlation_id": "e2e-portal-1",
    }


def test_readiness_and_dependencies_are_truthful_with_no_adapters(
    client: TestClient,
) -> None:
    ready = client.get("/health/ready")
    dependencies = client.get("/v1/system/dependencies")

    assert ready.status_code == 200
    assert ready.json()["status"] == "READY"
    assert ready.json()["dependencies"] == []
    assert dependencies.status_code == 200
    assert dependencies.json()["dependencies"] == []


def test_optional_dependency_failure_is_isolated(settings: PortalApiSettings) -> None:
    app = create_app(
        settings=settings,
        adapter_registry=AdapterRegistry((UnavailableOptionalAdapter(),)),
    )
    with TestClient(app) as client:
        live = client.get("/health/live")
        ready = client.get("/health/ready")

    assert live.status_code == 200
    assert ready.status_code == 200
    assert ready.json()["status"] == "DEGRADED"
    assert ready.json()["dependencies"][0]["status"] == "UNAVAILABLE"


def test_readiness_deadline_is_bounded(settings: PortalApiSettings) -> None:
    bounded_settings = settings.model_copy(
        update={
            "readiness_timeout_seconds": 0.01,
            "dependency_timeout_seconds": 1,
        }
    )
    app = create_app(
        settings=bounded_settings,
        adapter_registry=AdapterRegistry((SlowRequiredAdapter(),)),
    )
    with TestClient(app) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "NOT_READY"
    assert response.json()["reason"] == "Readiness evaluation exceeded its configured deadline."


def test_system_info_contains_only_allowlisted_metadata(client: TestClient) -> None:
    response = client.get("/v1/system/info")
    payload = response.json()
    serialized = response.text.lower()

    assert response.status_code == 200
    assert payload["runtime_environment"] == "test"
    assert payload["supported_api_versions"] == ["v1"]
    for forbidden in ("password", "database_url", "filesystem", "environment_variables"):
        assert forbidden not in serialized


def test_not_found_and_method_not_allowed_use_problem_details(client: TestClient) -> None:
    missing = client.get("/v1/not-present")
    method = client.post("/v1/system/info")

    assert missing.status_code == 404
    assert missing.json()["error_code"] == "RESOURCE_NOT_FOUND"
    assert missing.json()["correlation_id"] == missing.headers["X-Correlation-ID"]
    assert method.status_code == 405
    assert method.json()["error_code"] == "METHOD_NOT_ALLOWED"


def test_validation_error_is_problem_details(client: TestClient) -> None:
    response = client.get("/v1/system/dependencies?force=not-a-boolean")

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    assert response.json()["field_errors"][0]["field"] == "force"


def test_unknown_error_is_sanitized(settings: PortalApiSettings) -> None:
    app: FastAPI = create_app(settings=settings)

    @app.get("/test-only-failure")
    async def fail() -> None:
        raise RuntimeError("password=must-not-reach-response C:\\private\\path")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-only-failure")

    assert response.status_code == 500
    assert response.json()["error_code"] == "PORTAL_INTERNAL_ERROR"
    assert "must-not-reach-response" not in response.text
    assert "private" not in response.text


def test_security_headers_and_cors_are_applied(client: TestClient) -> None:
    response = client.get("/health/live")
    preflight = client.options(
        "/health/live",
        headers={
            "Origin": "http://portal.test",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert preflight.headers["access-control-allow-origin"] == "http://portal.test"


def test_untrusted_host_is_rejected(client: TestClient) -> None:
    response = client.get("/health/live", headers={"Host": "untrusted.example"})

    assert response.status_code == 400


def test_production_disables_openapi() -> None:
    settings = PortalApiSettings(
        environment=PortalEnvironment.PRODUCTION,
        openapi_enabled=False,
        log_format="json",
        allowed_origins="https://portal.example",
        trusted_hosts="portal-api.example",
        build_sha="abc123",
        build_time="2026-07-24T00:00:00Z",
    )
    with TestClient(create_app(settings=settings)) as client:
        openapi_response = client.get("/openapi.json", headers={"Host": "portal-api.example"})
        assert openapi_response.status_code == 404
        assert client.get("/docs", headers={"Host": "portal-api.example"}).status_code == 404


def test_no_environment_dump_endpoint_or_session_cookie(client: TestClient) -> None:
    response = client.get("/v1/system/environment")
    live = client.get("/health/live")

    assert response.status_code == 404
    assert "set-cookie" not in live.headers
