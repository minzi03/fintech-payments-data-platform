"""Operational OpenTelemetry pipeline and instrumentation tests."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import ClassVar

import httpx
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from portal_api.auth.oidc_provider import HttpxOidcProvider
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.db.unit_of_work import local_transaction
from portal_api.main import create_app
from portal_api.telemetry.metrics import NoopTelemetry
from portal_api.telemetry.otel import OpenTelemetryRuntime, build_telemetry
from prometheus_client import CollectorRegistry, generate_latest
from sqlalchemy import create_engine, text


def _settings(**updates: object) -> PortalApiSettings:
    values: dict[str, object] = {
        "_env_file": None,
        "environment": PortalEnvironment.TEST,
        "telemetry_enabled": True,
        "telemetry_metrics_exporter": "console",
        "telemetry_trace_exporter": "console",
        "telemetry_trace_sampling_ratio": 1,
        "telemetry_export_interval_seconds": 300,
        "telemetry_export_timeout_seconds": 2,
        "health_cache_ttl_seconds": 0,
        "trusted_hosts": "testserver,portal.test",
    }
    values.update(updates)
    return PortalApiSettings(**values)


def _metric_names(reader: InMemoryMetricReader) -> set[str]:
    data = reader.get_metrics_data()
    return {
        metric.name
        for resource_metrics in data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }


def test_disabled_telemetry_has_no_exporter_or_background_runtime() -> None:
    settings = PortalApiSettings(
        _env_file=None,
        telemetry_enabled=False,
        trusted_hosts="testserver,portal.test",
    )
    telemetry = build_telemetry(settings)
    app = create_app(settings=settings)

    assert isinstance(telemetry, NoopTelemetry)
    assert isinstance(app.state.telemetry, NoopTelemetry)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200


def test_prometheus_catalog_is_low_cardinality_and_complete() -> None:
    registry = CollectorRegistry(auto_describe=True)
    runtime = OpenTelemetryRuntime(
        _settings(
            telemetry_metrics_exporter="prometheus",
            telemetry_trace_exporter="none",
        ),
        prometheus_registry=registry,
        start_prometheus_server=False,
    )
    try:
        for _ in range(100):
            runtime.record_http("/health/ready", "GET", 200, 1.5)
        runtime.record_readiness("READY")
        runtime.record_dependency("portal-postgresql", "DOWN", 2.5)
        runtime.record_dependency("portal-postgresql", "UP", 1.5)
        runtime.record_oidc("discovery", "success", 3.5, cache_refresh=True)
        runtime.record_oidc_unknown_kid_refresh()
        runtime.record_oidc_validation_failure("id_token")
        runtime.record_database_connection("success", 0.5)
        runtime.record_database_query("SELECT", "success", 1.2)
        runtime.record_audit_event("auth.login_succeeded.v1", "SUCCEEDED")
        runtime.record_archive("success")
        runtime.record_outbox(
            operation="enqueued",
            destination="local_postgres",
            result="enqueued",
            failure_class="none",
            event_family="auth",
            attempt_bucket="0",
            duration_ms=0,
        )
        runtime.record_outbox(
            operation="delivery",
            destination="local_postgres",
            result="success",
            failure_class="none",
            event_family="auth",
            attempt_bucket="1",
            duration_ms=1.1,
        )
        runtime.record_outbox_backlog(
            pending=3,
            dead_lettered=1,
            oldest_pending_age_seconds=2.5,
        )
        runtime.record_maintenance(
            job_name="recover_outbox_leases",
            status="succeeded",
            rows_processed=2,
            duration_ms=0.7,
            failure_class="none",
        )
        runtime.record_maintenance_overdue(
            job_name="recover_outbox_leases",
            overdue=False,
        )
        runtime.record_abuse(
            operation="login",
            decision="throttled",
            policy="login-default",
            policy_version="v1",
            dimension="ip_prefix",
            backend_status="up",
            failure_class="none",
            penalty_level="temporarily_blocked",
            fallback_mode="inactive",
            duration_ms=0.8,
        )
        runtime.record_abuse_provider_concurrency(
            operation="token_refresh",
            outcome="throttled",
            backend_status="up",
            duration_ms=0.3,
        )
        rendered = generate_latest(registry).decode("utf-8")
    finally:
        runtime.shutdown()

    for metric in (
        "portal_http_requests_total",
        "portal_http_duration",
        "portal_auth_events_total",
        "portal_readiness_checks_total",
        "portal_readiness_dependency_checks_total",
        "portal_readiness_dependency_status",
        "portal_readiness_dependency_transitions_total",
        "portal_readiness_dependency_recovery_seconds",
        "portal_oidc_operation_duration",
        "portal_db_connection_wait",
        "portal_db_query_duration",
        "portal_audit_events_total",
        "portal_audit_outbox_backlog",
        "portal_audit_outbox_enqueued_total",
        "portal_audit_outbox_deliveries_total",
        "portal_audit_outbox_delivery_duration",
        "portal_audit_outbox_oldest_pending_age",
        "portal_audit_archive_total",
        "portal_maintenance_runs_total",
        "portal_maintenance_duration",
        "portal_maintenance_rows_processed_total",
        "portal_maintenance_overdue",
        "portal_abuse_requests_total",
        "portal_abuse_decisions_total",
        "portal_abuse_backend_duration",
        "portal_abuse_penalties_total",
        "portal_abuse_provider_concurrency",
        "portal_abuse_provider_throttled_total",
    ):
        assert metric in rendered
    assert 'event="login_success"' in rendered
    assert 'event="callback_success"' in rendered
    assert 'status="pending"' in rendered
    assert 'status="dead_lettered"' in rendered
    assert "request_id" not in rendered
    assert "trace_id" not in rendered
    assert "raw_ip" not in rendered
    assert "session_id" not in rendered
    assert "subject" not in rendered


def test_http_trace_propagation_response_correlation_and_metrics() -> None:
    reader = InMemoryMetricReader()
    spans = InMemorySpanExporter()
    runtime = OpenTelemetryRuntime(
        _settings(),
        metric_readers=[reader],
        span_exporters=[spans],
    )
    app = create_app(settings=_settings(), telemetry=runtime)
    inbound_trace_id = "1" * 32
    with TestClient(app) as client:
        response = client.get(
            "/health/live",
            headers={"traceparent": f"00-{inbound_trace_id}-{'2' * 16}-01"},
        )
        runtime.force_flush()
        names = _metric_names(reader)

    assert response.status_code == 200
    assert response.headers["x-trace-id"] == inbound_trace_id
    assert len(response.headers["x-span-id"]) == 16
    assert response.headers["x-request-id"]
    assert "portal.http.requests" in names
    server_span = next(
        span for span in spans.get_finished_spans() if span.name == "GET /health/live"
    )
    assert f"{server_span.context.trace_id:032x}" == inbound_trace_id
    assert server_span.attributes["portal.request_id"] == response.headers["x-request-id"]


def test_database_connection_query_metrics_and_child_span() -> None:
    reader = InMemoryMetricReader()
    spans = InMemorySpanExporter()
    runtime = OpenTelemetryRuntime(
        _settings(),
        metric_readers=[reader],
        span_exporters=[spans],
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    runtime.instrument_database(engine)
    try:
        with runtime.span("parent"), local_transaction(engine) as connection:
            assert connection.execute(text("SELECT 1")).scalar_one() == 1
        runtime.force_flush()
        names = _metric_names(reader)
    finally:
        runtime.shutdown()
        engine.dispose()

    assert "portal.db.connection.wait" in names
    assert "portal.db.query.duration" in names
    db_span = next(span for span in spans.get_finished_spans() if span.name == "db.select")
    assert db_span.parent is not None


def test_oidc_outbound_trace_context_and_metrics() -> None:
    reader = InMemoryMetricReader()
    spans = InMemorySpanExporter()
    runtime = OpenTelemetryRuntime(
        _settings(),
        metric_readers=[reader],
        span_exporters=[spans],
    )
    observed_traceparent: list[str] = []
    issuer = "https://identity.test/realms/portal"

    def handler(request: httpx.Request) -> httpx.Response:
        observed_traceparent.append(request.headers["traceparent"])
        return httpx.Response(
            200,
            json={
                "issuer": issuer,
                "authorization_endpoint": f"{issuer}/auth",
                "token_endpoint": f"{issuer}/token",
                "jwks_uri": f"{issuer}/certs",
                "token_endpoint_auth_methods_supported": ["client_secret_basic"],
            },
        )

    provider = HttpxOidcProvider(
        PortalApiSettings(
            _env_file=None,
            environment=PortalEnvironment.TEST,
            oidc_issuer=issuer,
            oidc_client_secret="test-secret",
            oidc_redirect_uri="https://portal.test/callback",
        ),
        transport=httpx.MockTransport(handler),
        telemetry=runtime,
    )
    try:
        with runtime.span("authentication.flow"):
            config = asyncio.run(provider.get_config())
        runtime.force_flush()
        names = _metric_names(reader)
    finally:
        runtime.shutdown()

    assert config.issuer == issuer
    assert observed_traceparent
    assert "portal.oidc.operation.duration" in names
    assert {span.name for span in spans.get_finished_spans()} >= {
        "authentication.flow",
        "oidc.discovery",
    }


class _OtlpHandler(BaseHTTPRequestHandler):
    records: ClassVar[list[tuple[str, bytes]]] = []
    lock: ClassVar[Lock] = Lock()

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        with self.lock:
            self.records.append((self.path, body))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _otlp_server() -> Iterator[tuple[ThreadingHTTPServer, str]]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _OtlpHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_otlp_http_metrics_and_traces_are_exported() -> None:
    _OtlpHandler.records = []
    with _otlp_server() as (_, endpoint):
        runtime = OpenTelemetryRuntime(
            _settings(
                telemetry_metrics_exporter="otlp",
                telemetry_trace_exporter="otlp",
                telemetry_otlp_endpoint=endpoint,
            )
        )
        app = create_app(settings=_settings(), telemetry=runtime)
        with TestClient(app) as client:
            response = client.get("/health/live")
            assert response.status_code == 200
            runtime.force_flush()

    paths = {path for path, body in _OtlpHandler.records if body}
    assert "/v1/metrics" in paths
    assert "/v1/traces" in paths
