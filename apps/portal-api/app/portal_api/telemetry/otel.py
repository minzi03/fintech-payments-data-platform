"""OpenTelemetry metrics, traces, exporters, and bounded instrumentation."""

from __future__ import annotations

import socket
from collections.abc import Mapping, MutableMapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from threading import Lock, Thread
from time import monotonic, perf_counter
from typing import Any
from wsgiref.simple_server import WSGIServer

from opentelemetry import context as otel_context
from opentelemetry import propagate, trace
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.exporter.prometheus import PrometheusMetricReader
from opentelemetry.metrics import Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    MetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from prometheus_client import CollectorRegistry, start_http_server
from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine, ExceptionContext

from portal_api.core.config import (
    PortalApiSettings,
    TelemetryMetricsExporter,
    TelemetryTraceExporter,
)
from portal_api.telemetry.database import bind_engine_telemetry
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder, domain_metric_event

_QUERY_STACK_KEY = "portal_telemetry_query_stack"
_DATABASE_OPERATIONS = frozenset({"SELECT", "INSERT", "UPDATE", "DELETE", "CALL", "OTHER"})


@dataclass(slots=True)
class _DependencyState:
    status: str
    first_failed_at: float | None
    checks: int
    successes: int


class OpenTelemetryRuntime:
    """One process-local OpenTelemetry SDK pipeline."""

    def __init__(
        self,
        settings: PortalApiSettings,
        *,
        metric_readers: Sequence[MetricReader] | None = None,
        span_exporters: Sequence[SpanExporter] | None = None,
        prometheus_registry: CollectorRegistry | None = None,
        start_prometheus_server: bool = True,
    ) -> None:
        self._settings = settings
        self._lock = Lock()
        self._started = False
        self._shutdown = False
        self._start_prometheus_server = start_prometheus_server
        self._prometheus_registry = prometheus_registry
        self._prometheus_server: WSGIServer | None = None
        self._prometheus_thread: Thread | None = None
        self._engine: Engine | None = None
        self._database_listeners: list[tuple[str, Any]] = []
        self._dependency_states: dict[str, _DependencyState] = {}
        self._overall_readiness = "UNKNOWN"
        self._audit_outbox_backlog = 0

        resource_attributes: dict[str, str] = {
            "service.name": settings.service_name,
            "service.version": settings.service_version,
            "service.instance.id": socket.gethostname(),
            "deployment.environment.name": settings.environment.value,
            "portal.build.sha": settings.build_sha,
        }
        resource_attributes.update(settings.telemetry_resource_attribute_values)
        resource = Resource.create(resource_attributes)

        readers = (
            list(metric_readers)
            if metric_readers is not None
            else self._configured_metric_readers()
        )
        self.meter_provider = MeterProvider(
            metric_readers=readers,
            resource=resource,
            shutdown_on_exit=False,
        )
        self.tracer_provider = TracerProvider(
            sampler=ParentBased(TraceIdRatioBased(settings.telemetry_trace_sampling_ratio)),
            resource=resource,
            shutdown_on_exit=False,
            meter_provider=self.meter_provider,
        )
        exporters = (
            list(span_exporters)
            if span_exporters is not None
            else self._configured_span_exporters()
        )
        for exporter in exporters:
            if span_exporters is None:
                self.tracer_provider.add_span_processor(
                    BatchSpanProcessor(
                        exporter,
                        export_timeout_millis=settings.telemetry_export_timeout_seconds * 1000,
                        meter_provider=self.meter_provider,
                    )
                )
            else:
                self.tracer_provider.add_span_processor(
                    SimpleSpanProcessor(exporter, meter_provider=self.meter_provider)
                )
        self._tracer = self.tracer_provider.get_tracer(
            "fintech.portal-api",
            settings.service_version,
        )
        self._meter = self.meter_provider.get_meter(
            "fintech.portal-api",
            settings.service_version,
        )
        self._create_instruments()

    @property
    def prometheus_port(self) -> int | None:
        return (
            int(self._prometheus_server.server_port)
            if self._prometheus_server is not None
            else None
        )

    @property
    def prometheus_registry(self) -> CollectorRegistry | None:
        return self._prometheus_registry

    def _configured_metric_readers(self) -> list[MetricReader]:
        exporter = self._settings.telemetry_metrics_exporter
        if exporter is TelemetryMetricsExporter.NONE:
            return []
        if exporter is TelemetryMetricsExporter.PROMETHEUS:
            self._prometheus_registry = self._prometheus_registry or CollectorRegistry(
                auto_describe=True
            )
            return [PrometheusMetricReader(registry=self._prometheus_registry)]
        if exporter is TelemetryMetricsExporter.CONSOLE:
            metric_exporter: MetricExporter = ConsoleMetricExporter()
        else:
            metric_exporter = OTLPMetricExporter(
                endpoint=self._otlp_endpoint("metrics"),
                timeout=self._settings.telemetry_export_timeout_seconds,
            )
        return [
            PeriodicExportingMetricReader(
                metric_exporter,
                export_interval_millis=self._settings.telemetry_export_interval_seconds * 1000,
                export_timeout_millis=self._settings.telemetry_export_timeout_seconds * 1000,
            )
        ]

    def _configured_span_exporters(self) -> list[SpanExporter]:
        exporter = self._settings.telemetry_trace_exporter
        if exporter is TelemetryTraceExporter.NONE:
            return []
        if exporter is TelemetryTraceExporter.CONSOLE:
            return [ConsoleSpanExporter()]
        return [
            OTLPSpanExporter(
                endpoint=self._otlp_endpoint("traces"),
                timeout=self._settings.telemetry_export_timeout_seconds,
            )
        ]

    def _otlp_endpoint(self, signal: str) -> str:
        return f"{self._settings.telemetry_otlp_endpoint.rstrip('/')}/v1/{signal}"

    def _create_instruments(self) -> None:
        self._http_requests = self._meter.create_counter(
            "portal.http.requests",
            description="Portal HTTP requests",
            unit="{request}",
        )
        self._http_errors = self._meter.create_counter(
            "portal.http.errors",
            description="Portal HTTP responses with status >= 400",
            unit="{error}",
        )
        self._http_duration = self._meter.create_histogram(
            "portal.http.duration",
            description="Portal HTTP request duration",
            unit="ms",
        )
        self._auth_events = self._meter.create_counter(
            "portal.auth.events",
            description="Authentication lifecycle events",
            unit="{event}",
        )
        self._session_events = self._meter.create_counter(
            "portal.session.events",
            description="Server-session lifecycle events",
            unit="{event}",
        )
        self._readiness_checks = self._meter.create_counter(
            "portal.readiness.checks",
            description="Overall readiness evaluations",
            unit="{check}",
        )
        self._dependency_checks = self._meter.create_counter(
            "portal.readiness.dependency.checks",
            description="Dependency readiness checks",
            unit="{check}",
        )
        self._dependency_duration = self._meter.create_histogram(
            "portal.readiness.dependency.duration",
            description="Dependency readiness check duration",
            unit="ms",
        )
        self._dependency_status = self._meter.create_gauge(
            "portal.readiness.dependency.status",
            description="Latest dependency status (1 up, 0 otherwise)",
            unit="1",
        )
        self._dependency_timeouts = self._meter.create_counter(
            "portal.readiness.dependency.timeouts",
            description="Dependency readiness timeouts",
            unit="{timeout}",
        )
        self._dependency_transitions = self._meter.create_counter(
            "portal.readiness.dependency.transitions",
            description="Dependency readiness state transitions",
            unit="{transition}",
        )
        self._dependency_recovery = self._meter.create_histogram(
            "portal.readiness.dependency.recovery",
            description="Elapsed time from dependency failure to recovery",
            unit="s",
        )
        self._oidc_duration = self._meter.create_histogram(
            "portal.oidc.operation.duration",
            description="OIDC discovery, JWKS, and token operation duration",
            unit="ms",
        )
        self._oidc_cache_refreshes = self._meter.create_counter(
            "portal.oidc.cache.refreshes",
            description="OIDC cache refresh operations",
            unit="{refresh}",
        )
        self._oidc_unknown_kid_refreshes = self._meter.create_counter(
            "portal.oidc.unknown_kid.refreshes",
            description="Forced JWKS refreshes caused by unknown key identifiers",
            unit="{refresh}",
        )
        self._oidc_validation_failures = self._meter.create_counter(
            "portal.oidc.validation.failures",
            description="OIDC token validation failures",
            unit="{failure}",
        )
        self._provider_session_operations = self._meter.create_counter(
            "portal.provider.session.operations",
            description="Provider-backed session lifecycle operations",
            unit="{operation}",
        )
        self._provider_session_duration = self._meter.create_histogram(
            "portal.provider.session.duration",
            description="Provider-backed session lifecycle operation latency",
            unit="ms",
        )
        self._provider_session_retries = self._meter.create_counter(
            "portal.provider.session.retries",
            description="Provider token refresh retry attempts",
            unit="{retry}",
        )
        self._provider_session_transitions = self._meter.create_counter(
            "portal.provider.session.transitions",
            description="Provider session state transitions",
            unit="{transition}",
        )
        self._abuse_requests = self._meter.create_counter(
            "portal.abuse.requests",
            description="Abuse-protection evaluations",
            unit="{request}",
        )
        self._abuse_decisions = self._meter.create_counter(
            "portal.abuse.decisions",
            description="Abuse-protection decisions",
            unit="{decision}",
        )
        self._abuse_backend_duration = self._meter.create_histogram(
            "portal.abuse.backend.duration",
            description="Distributed abuse backend latency",
            unit="ms",
        )
        self._abuse_backend_failures = self._meter.create_counter(
            "portal.abuse.backend.failures",
            description="Distributed abuse backend failures",
            unit="{failure}",
        )
        self._abuse_penalties = self._meter.create_counter(
            "portal.abuse.penalties",
            description="Abuse penalty outcomes",
            unit="{penalty}",
        )
        self._abuse_fallback_activations = self._meter.create_counter(
            "portal.abuse.fallback.activations",
            description="Process-local abuse fallback activations",
            unit="{activation}",
        )
        self._abuse_provider_concurrency = self._meter.create_histogram(
            "portal.abuse.provider.concurrency",
            description="Provider concurrency acquisition latency",
            unit="ms",
        )
        self._abuse_provider_throttled = self._meter.create_counter(
            "portal.abuse.provider.throttled",
            description="Provider operations rejected by bounded concurrency",
            unit="{operation}",
        )
        self._database_connection_wait = self._meter.create_histogram(
            "portal.db.connection.wait",
            description="Database pool checkout and connection acquisition latency",
            unit="ms",
        )
        self._database_connection_failures = self._meter.create_counter(
            "portal.db.connection.failures",
            description="Database connection acquisition failures",
            unit="{failure}",
        )
        self._database_query_duration = self._meter.create_histogram(
            "portal.db.query.duration",
            description="Bounded database query latency by operation",
            unit="ms",
        )
        self._audit_events = self._meter.create_counter(
            "portal.audit.events",
            description="Security audit ledger events persisted",
            unit="{event}",
        )
        self._audit_archive = self._meter.create_counter(
            "portal.audit.archive",
            description="Audit archive delivery outcomes",
            unit="{event}",
        )
        self._meter.create_observable_gauge(
            "portal.readiness.overall",
            callbacks=[self._observe_overall_readiness],
            description="Current overall readiness (1 ready, 0 not ready)",
            unit="1",
        )
        self._meter.create_observable_gauge(
            "portal.readiness.dependency.availability",
            callbacks=[self._observe_dependency_availability],
            description="Process-lifetime dependency check success ratio",
            unit="1",
        )
        self._meter.create_observable_gauge(
            "portal.readiness.dependency.failure.duration",
            callbacks=[self._observe_dependency_failure_duration],
            description="Current continuous dependency failure duration",
            unit="s",
        )
        self._meter.create_observable_gauge(
            "portal.db.pool.connections",
            callbacks=[self._observe_database_pool],
            description="SQLAlchemy pool connection utilization",
            unit="{connection}",
        )
        self._meter.create_observable_gauge(
            "portal.session.active",
            callbacks=[self._observe_active_sessions],
            description="Active or refresh-required durable sessions",
            unit="{session}",
        )
        self._meter.create_observable_gauge(
            "portal.audit.outbox.backlog",
            callbacks=[self._observe_audit_backlog],
            description="Undelivered audit archive outbox records",
            unit="{event}",
        )

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            if (
                self._settings.telemetry_metrics_exporter is TelemetryMetricsExporter.PROMETHEUS
                and self._start_prometheus_server
            ):
                if self._prometheus_registry is None:
                    raise RuntimeError("Prometheus registry is unavailable")
                server, thread = start_http_server(
                    port=self._settings.telemetry_prometheus_port,
                    addr=self._settings.telemetry_prometheus_host,
                    registry=self._prometheus_registry,
                )
                self._prometheus_server = server
                self._prometheus_thread = thread
            self._started = True

    def shutdown(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
        if self._prometheus_server is not None:
            self._prometheus_server.shutdown()
            self._prometheus_server.server_close()
        if self._prometheus_thread is not None:
            self._prometheus_thread.join(timeout=self._settings.telemetry_export_timeout_seconds)
        self.tracer_provider.force_flush(
            timeout_millis=int(self._settings.telemetry_export_timeout_seconds * 1000)
        )
        self.meter_provider.force_flush(
            timeout_millis=self._settings.telemetry_export_timeout_seconds * 1000
        )
        self.tracer_provider.shutdown()
        self.meter_provider.shutdown()

    def force_flush(self) -> None:
        self.tracer_provider.force_flush(
            timeout_millis=int(self._settings.telemetry_export_timeout_seconds * 1000)
        )
        self.meter_provider.force_flush(
            timeout_millis=self._settings.telemetry_export_timeout_seconds * 1000
        )

    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, str | bool | int | float] | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        context: Context | None = None,
    ) -> AbstractContextManager[Span]:
        return self._tracer.start_as_current_span(
            name,
            kind=kind,
            attributes=attributes,
            context=context,
        )

    def extract(self, headers: Mapping[str, str]) -> Context:
        return propagate.extract(headers)

    def inject(self, headers: MutableMapping[str, str]) -> None:
        propagate.inject(headers)

    def record_http(self, route: str, method: str, status_code: int, duration_ms: float) -> None:
        attributes: dict[str, str | int] = {
            "http.request.method": method,
            "http.route": route,
            "http.response.status_code": status_code,
        }
        self._http_requests.add(1, attributes)
        self._http_duration.record(duration_ms, attributes)
        if status_code >= 400:
            self._http_errors.add(
                1,
                {
                    "http.request.method": method,
                    "http.route": route,
                    "error.type": f"http_{status_code // 100}xx",
                },
            )

    def record_readiness(self, status: str) -> None:
        self._readiness_checks.add(1, {"status": status})
        with self._lock:
            self._overall_readiness = status

    def record_dependency(self, dependency_id: str, status: str, duration_ms: float) -> None:
        attributes = {"dependency": dependency_id, "status": status}
        self._dependency_checks.add(1, attributes)
        self._dependency_duration.record(duration_ms, attributes)
        self._dependency_status.set(
            1 if status == "UP" else 0,
            {"dependency": dependency_id},
        )
        if status == "TIMEOUT":
            self._dependency_timeouts.add(1, {"dependency": dependency_id})
        now = monotonic()
        with self._lock:
            previous = self._dependency_states.get(dependency_id)
            checks = 1 if previous is None else previous.checks + 1
            successes = (0 if previous is None else previous.successes) + int(status == "UP")
            failed_at = (
                None
                if status == "UP"
                else (
                    now
                    if previous is None or previous.first_failed_at is None
                    else previous.first_failed_at
                )
            )
            if previous is not None and previous.status != status:
                self._dependency_transitions.add(
                    1,
                    {
                        "dependency": dependency_id,
                        "from": previous.status,
                        "to": status,
                    },
                )
                if status == "UP" and previous.first_failed_at is not None:
                    self._dependency_recovery.record(
                        now - previous.first_failed_at,
                        {"dependency": dependency_id},
                    )
            self._dependency_states[dependency_id] = _DependencyState(
                status=status,
                first_failed_at=failed_at,
                checks=checks,
                successes=successes,
            )

    def record_oidc(
        self,
        operation: str,
        outcome: str,
        duration_ms: float,
        *,
        cache_refresh: bool = False,
    ) -> None:
        attributes = {"operation": operation, "outcome": outcome}
        self._oidc_duration.record(duration_ms, attributes)
        if cache_refresh:
            self._oidc_cache_refreshes.add(1, {"operation": operation})

    def record_oidc_unknown_kid_refresh(self) -> None:
        self._oidc_unknown_kid_refreshes.add(1)

    def record_oidc_validation_failure(self, category: str) -> None:
        self._oidc_validation_failures.add(1, {"category": category})

    def record_provider_session(
        self,
        operation: str,
        outcome: str,
        duration_ms: float,
        *,
        retry_count: int = 0,
        from_state: str | None = None,
        to_state: str | None = None,
    ) -> None:
        attributes = {"operation": operation, "outcome": outcome}
        self._provider_session_operations.add(1, attributes)
        self._provider_session_duration.record(duration_ms, attributes)
        if retry_count:
            self._provider_session_retries.add(
                retry_count,
                {"operation": operation},
            )
        if from_state is not None and to_state is not None:
            self._provider_session_transitions.add(
                1,
                {"from": from_state, "to": to_state},
            )

    def record_abuse(
        self,
        *,
        operation: str,
        decision: str,
        policy: str,
        policy_version: str,
        dimension: str,
        backend_status: str,
        failure_class: str,
        penalty_level: str,
        fallback_mode: str,
        duration_ms: float,
    ) -> None:
        common = {
            "operation": operation,
            "policy": policy,
            "policy_version": policy_version,
        }
        self._abuse_requests.add(1, common)
        self._abuse_decisions.add(
            1,
            {
                **common,
                "decision": decision,
                "dimension": dimension,
                "backend_status": backend_status,
            },
        )
        self._abuse_backend_duration.record(
            duration_ms,
            {
                "operation": operation,
                "backend_status": backend_status,
            },
        )
        if failure_class != "none":
            self._abuse_backend_failures.add(
                1,
                {
                    "operation": operation,
                    "failure_class": failure_class,
                },
            )
        if penalty_level != "normal":
            self._abuse_penalties.add(
                1,
                {
                    "operation": operation,
                    "penalty_level": penalty_level,
                },
            )
        if fallback_mode == "active":
            self._abuse_fallback_activations.add(
                1,
                {
                    "operation": operation,
                    "fallback_mode": fallback_mode,
                },
            )

    def record_abuse_provider_concurrency(
        self,
        *,
        operation: str,
        outcome: str,
        backend_status: str,
        duration_ms: float,
    ) -> None:
        attributes = {
            "provider_operation": operation,
            "backend_status": backend_status,
            "outcome": outcome,
        }
        self._abuse_provider_concurrency.record(duration_ms, attributes)
        if outcome == "throttled":
            self._abuse_provider_throttled.add(1, attributes)

    def record_database_connection(self, outcome: str, duration_ms: float) -> None:
        self._database_connection_wait.record(duration_ms, {"outcome": outcome})
        if outcome != "success":
            self._database_connection_failures.add(1, {"outcome": outcome})

    def record_database_query(self, operation: str, outcome: str, duration_ms: float) -> None:
        bounded_operation = operation if operation in _DATABASE_OPERATIONS else "OTHER"
        self._database_query_duration.record(
            duration_ms,
            {"operation": bounded_operation, "outcome": outcome},
        )

    def record_audit_event(self, event_type: str, outcome: str) -> None:
        self._audit_events.add(1, {"event": event_type, "outcome": outcome})
        if outcome == "SUCCEEDED":
            with self._lock:
                self._audit_outbox_backlog += 1
        auth_events, session_event = domain_metric_event(event_type)
        for auth_event in auth_events:
            self._auth_events.add(1, {"event": auth_event, "outcome": outcome})
        if session_event is not None:
            self._session_events.add(1, {"event": session_event, "outcome": outcome})

    def record_archive(self, outcome: str) -> None:
        self._audit_archive.add(1, {"outcome": outcome})
        if outcome == "success":
            with self._lock:
                self._audit_outbox_backlog = max(0, self._audit_outbox_backlog - 1)

    def instrument_database(self, engine: Engine) -> None:
        if self._engine is engine:
            return
        if self._engine is not None:
            raise RuntimeError("Telemetry runtime supports one Portal database engine")
        self._engine = engine
        bind_engine_telemetry(engine, self)

        def before_cursor_execute(
            connection: Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            del cursor, parameters, executemany
            if context.execution_options.get("portal_telemetry_skip"):
                return
            operation = self._sql_operation(statement)
            span = self._tracer.start_span(
                f"db.{operation.casefold()}",
                kind=SpanKind.CLIENT,
                attributes={
                    "db.system.name": "postgresql",
                    "db.operation.name": operation,
                },
            )
            token = otel_context.attach(trace.set_span_in_context(span))
            stack = connection.info.setdefault(_QUERY_STACK_KEY, [])
            stack.append((perf_counter(), operation, span, token))

        def after_cursor_execute(
            connection: Connection,
            cursor: Any,
            statement: str,
            parameters: Any,
            context: Any,
            executemany: bool,
        ) -> None:
            del cursor, statement, parameters, context, executemany
            self._finish_database_span(connection, "success")

        def handle_error(exception_context: ExceptionContext) -> None:
            connection = exception_context.connection
            if connection is not None:
                self._finish_database_span(connection, "error", failed=True)

        for name, listener in (
            ("before_cursor_execute", before_cursor_execute),
            ("after_cursor_execute", after_cursor_execute),
            ("handle_error", handle_error),
        ):
            event.listen(engine, name, listener)
            self._database_listeners.append((name, listener))

    def _finish_database_span(
        self,
        connection: Connection,
        outcome: str,
        *,
        failed: bool = False,
    ) -> None:
        stack = connection.info.get(_QUERY_STACK_KEY, [])
        if not stack:
            return
        started, operation, span, token = stack.pop()
        duration_ms = (perf_counter() - started) * 1000
        self.record_database_query(operation, outcome, duration_ms)
        if failed:
            span.set_status(Status(StatusCode.ERROR))
        otel_context.detach(token)
        span.end()

    @staticmethod
    def _sql_operation(statement: str) -> str:
        parts = statement.lstrip().split(None, 1)
        operation = parts[0].upper() if parts else "OTHER"
        return operation if operation in _DATABASE_OPERATIONS else "OTHER"

    def _observe_overall_readiness(self, options: Any) -> list[Observation]:
        del options
        with self._lock:
            return [Observation(1 if self._overall_readiness == "READY" else 0)]

    def _observe_dependency_availability(self, options: Any) -> list[Observation]:
        del options
        with self._lock:
            return [
                Observation(
                    state.successes / state.checks if state.checks else 0,
                    {"dependency": dependency},
                )
                for dependency, state in sorted(self._dependency_states.items())
            ]

    def _observe_dependency_failure_duration(self, options: Any) -> list[Observation]:
        del options
        now = monotonic()
        with self._lock:
            return [
                Observation(
                    0 if state.first_failed_at is None else now - state.first_failed_at,
                    {"dependency": dependency},
                )
                for dependency, state in sorted(self._dependency_states.items())
            ]

    def _observe_database_pool(self, options: Any) -> list[Observation]:
        del options
        engine = self._engine
        if engine is None:
            return []
        observations: list[Observation] = []
        for state, method_name in (
            ("checked_out", "checkedout"),
            ("checked_in", "checkedin"),
            ("size", "size"),
            ("overflow", "overflow"),
        ):
            method = getattr(engine.pool, method_name, None)
            if callable(method):
                observations.append(Observation(method(), {"state": state}))
        return observations

    def _observe_active_sessions(self, options: Any) -> list[Observation]:
        del options
        value = self._database_scalar(
            """
            SELECT count(*)
            FROM portal_control.portal_sessions
            WHERE status IN ('ACTIVE', 'REFRESH_REQUIRED')
            """
        )
        return [] if value is None else [Observation(value)]

    def _observe_audit_backlog(self, options: Any) -> list[Observation]:
        del options
        with self._lock:
            return [Observation(self._audit_outbox_backlog, {"scope": "process"})]

    def _database_scalar(self, statement: str) -> int | None:
        engine = self._engine
        if engine is None:
            return None
        try:
            with engine.connect().execution_options(portal_telemetry_skip=True) as connection:
                return int(connection.execute(text(statement)).scalar_one())
        except Exception:
            return None


def build_telemetry(settings: PortalApiSettings) -> TelemetryRecorder:
    """Build exactly one configured process telemetry implementation."""
    if not settings.telemetry_enabled:
        return NoopTelemetry()
    return OpenTelemetryRuntime(settings)
