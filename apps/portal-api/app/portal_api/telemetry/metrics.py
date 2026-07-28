"""Vendor-neutral Portal telemetry boundary and deterministic test recorder."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, MutableMapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol

from opentelemetry.context import Context
from opentelemetry.trace import SpanKind

if TYPE_CHECKING:
    from sqlalchemy.engine import Engine

_AUTH_EVENT_MAP = {
    "auth.login_started.v1": ("login_attempt",),
    "auth.login_succeeded.v1": ("login_success", "callback_success"),
    "auth.login_failed.v1": ("login_failure", "callback_failure"),
    "auth.session_refreshed.v1": ("refresh",),
    "auth.provider_refresh_started.v1": ("refresh_attempt",),
    "auth.provider_refresh_succeeded.v1": ("refresh_success",),
    "auth.provider_refresh_failed.v1": ("refresh_failure",),
    "auth.provider_revocation_succeeded.v1": ("provider_revocation",),
    "auth.provider_revocation_failed.v1": ("provider_revocation",),
    "auth.provider_logout_succeeded.v1": ("provider_logout",),
    "auth.provider_logout_failed.v1": ("provider_logout",),
    "auth.logout_requested.v1": ("logout",),
    "auth.logout_completed.v1": ("logout",),
}
_SESSION_EVENT_MAP = {
    "auth.session_created.v1": "creation",
    "auth.session_rotated.v1": "rotation",
    "auth.session_refreshed.v1": "refresh",
    "auth.session_expired_idle.v1": "expiration",
    "auth.session_expired_absolute.v1": "expiration",
    "auth.session_revoked.v1": "revocation",
}


def domain_metric_event(event_type: str) -> tuple[tuple[str, ...], str | None]:
    """Map a bounded audit type to authentication/session metric event names."""
    return _AUTH_EVENT_MAP.get(event_type, ()), _SESSION_EVENT_MAP.get(event_type)


class TelemetryRecorder(Protocol):
    """Operational metrics and tracing boundary used by runtime components."""

    def start(self) -> None: ...

    def shutdown(self) -> None: ...

    def instrument_database(self, engine: Engine) -> None: ...

    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, str | bool | int | float] | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        context: Context | None = None,
    ) -> AbstractContextManager[Any]: ...

    def extract(self, headers: Mapping[str, str]) -> Context: ...

    def inject(self, headers: MutableMapping[str, str]) -> None: ...

    def record_http(
        self, route: str, method: str, status_code: int, duration_ms: float
    ) -> None: ...

    def record_readiness(self, status: str) -> None: ...

    def record_dependency(self, dependency_id: str, status: str, duration_ms: float) -> None: ...

    def record_oidc(
        self,
        operation: str,
        outcome: str,
        duration_ms: float,
        *,
        cache_refresh: bool = False,
    ) -> None: ...

    def record_oidc_unknown_kid_refresh(self) -> None: ...

    def record_oidc_validation_failure(self, category: str) -> None: ...

    def record_provider_session(
        self,
        operation: str,
        outcome: str,
        duration_ms: float,
        *,
        retry_count: int = 0,
        from_state: str | None = None,
        to_state: str | None = None,
    ) -> None: ...

    def record_database_connection(self, outcome: str, duration_ms: float) -> None: ...

    def record_database_query(self, operation: str, outcome: str, duration_ms: float) -> None: ...

    def record_audit_event(self, event_type: str, outcome: str) -> None: ...

    def record_archive(self, outcome: str) -> None: ...


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    http_requests: dict[str, int]
    http_failures: dict[str, int]
    readiness: dict[str, int]
    dependency_checks: dict[str, int]
    auth_events: dict[str, int]
    session_events: dict[str, int]
    oidc_operations: dict[str, int]
    provider_session_operations: dict[str, int]
    database_operations: dict[str, int]
    audit_events: dict[str, int]
    archive_events: dict[str, int]
    durations_ms: dict[str, tuple[float, ...]]


class InMemoryTelemetry:
    """Thread-safe recorder used by tests without starting exporter threads."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._http_requests: Counter[str] = Counter()
        self._http_failures: Counter[str] = Counter()
        self._readiness: Counter[str] = Counter()
        self._dependency_checks: Counter[str] = Counter()
        self._auth_events: Counter[str] = Counter()
        self._session_events: Counter[str] = Counter()
        self._oidc_operations: Counter[str] = Counter()
        self._provider_session_operations: Counter[str] = Counter()
        self._database_operations: Counter[str] = Counter()
        self._audit_events: Counter[str] = Counter()
        self._archive_events: Counter[str] = Counter()
        self._durations: dict[str, list[float]] = defaultdict(list)

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def instrument_database(self, engine: Engine) -> None:
        from portal_api.telemetry.database import bind_engine_telemetry

        bind_engine_telemetry(engine, self)

    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, str | bool | int | float] | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        context: Context | None = None,
    ) -> AbstractContextManager[Any]:
        del name, attributes, kind, context
        return nullcontext()

    def extract(self, headers: Mapping[str, str]) -> Context:
        del headers
        return Context()

    def inject(self, headers: MutableMapping[str, str]) -> None:
        del headers

    def record_http(self, route: str, method: str, status_code: int, duration_ms: float) -> None:
        key = f"{method}:{route}:{status_code}"
        with self._lock:
            self._http_requests[key] += 1
            if status_code >= 400:
                self._http_failures[f"{method}:{route}"] += 1
            self._durations[f"http:{method}:{route}"].append(duration_ms)

    def record_readiness(self, status: str) -> None:
        with self._lock:
            self._readiness[status] += 1

    def record_dependency(self, dependency_id: str, status: str, duration_ms: float) -> None:
        with self._lock:
            self._dependency_checks[f"{dependency_id}:{status}"] += 1
            self._durations[f"dependency:{dependency_id}"].append(duration_ms)

    def record_oidc(
        self,
        operation: str,
        outcome: str,
        duration_ms: float,
        *,
        cache_refresh: bool = False,
    ) -> None:
        with self._lock:
            self._oidc_operations[f"{operation}:{outcome}"] += 1
            if cache_refresh:
                self._oidc_operations[f"{operation}:cache_refresh"] += 1
            self._durations[f"oidc:{operation}"].append(duration_ms)

    def record_oidc_unknown_kid_refresh(self) -> None:
        with self._lock:
            self._oidc_operations["jwks:unknown_kid_refresh"] += 1

    def record_oidc_validation_failure(self, category: str) -> None:
        with self._lock:
            self._oidc_operations[f"validation:{category}"] += 1

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
        with self._lock:
            self._provider_session_operations[f"{operation}:{outcome}"] += 1
            if retry_count:
                self._provider_session_operations[f"{operation}:retry"] += retry_count
            if from_state is not None and to_state is not None:
                self._provider_session_operations[f"transition:{from_state}:{to_state}"] += 1
            self._durations[f"provider_session:{operation}"].append(duration_ms)

    def record_database_connection(self, outcome: str, duration_ms: float) -> None:
        with self._lock:
            self._database_operations[f"connection:{outcome}"] += 1
            self._durations["database:connection_wait"].append(duration_ms)

    def record_database_query(self, operation: str, outcome: str, duration_ms: float) -> None:
        with self._lock:
            self._database_operations[f"query:{operation}:{outcome}"] += 1
            self._durations[f"database:query:{operation}"].append(duration_ms)

    def record_audit_event(self, event_type: str, outcome: str) -> None:
        auth_events, session_event = domain_metric_event(event_type)
        with self._lock:
            self._audit_events[f"{event_type}:{outcome}"] += 1
            for auth_event in auth_events:
                self._auth_events[f"{auth_event}:{outcome}"] += 1
            if session_event is not None:
                self._session_events[f"{session_event}:{outcome}"] += 1

    def record_archive(self, outcome: str) -> None:
        with self._lock:
            self._archive_events[outcome] += 1

    def snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return TelemetrySnapshot(
                http_requests=dict(self._http_requests),
                http_failures=dict(self._http_failures),
                readiness=dict(self._readiness),
                dependency_checks=dict(self._dependency_checks),
                auth_events=dict(self._auth_events),
                session_events=dict(self._session_events),
                oidc_operations=dict(self._oidc_operations),
                provider_session_operations=dict(self._provider_session_operations),
                database_operations=dict(self._database_operations),
                audit_events=dict(self._audit_events),
                archive_events=dict(self._archive_events),
                durations_ms={key: tuple(values) for key, values in self._durations.items()},
            )


class NoopTelemetry:
    """Allocation-light disabled implementation."""

    def start(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def instrument_database(self, engine: Engine) -> None:
        del engine

    def span(
        self,
        name: str,
        *,
        attributes: Mapping[str, str | bool | int | float] | None = None,
        kind: SpanKind = SpanKind.INTERNAL,
        context: Context | None = None,
    ) -> AbstractContextManager[Any]:
        del name, attributes, kind, context
        return nullcontext()

    def extract(self, headers: Mapping[str, str]) -> Context:
        del headers
        return Context()

    def inject(self, headers: MutableMapping[str, str]) -> None:
        del headers

    def record_http(self, route: str, method: str, status_code: int, duration_ms: float) -> None:
        return None

    def record_readiness(self, status: str) -> None:
        return None

    def record_dependency(self, dependency_id: str, status: str, duration_ms: float) -> None:
        return None

    def record_oidc(
        self,
        operation: str,
        outcome: str,
        duration_ms: float,
        *,
        cache_refresh: bool = False,
    ) -> None:
        return None

    def record_oidc_unknown_kid_refresh(self) -> None:
        return None

    def record_oidc_validation_failure(self, category: str) -> None:
        return None

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
        return None

    def record_database_connection(self, outcome: str, duration_ms: float) -> None:
        return None

    def record_database_query(self, operation: str, outcome: str, duration_ms: float) -> None:
        return None

    def record_audit_event(self, event_type: str, outcome: str) -> None:
        return None

    def record_archive(self, outcome: str) -> None:
        return None
