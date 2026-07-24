"""Low-cardinality telemetry abstraction ready for an OpenTelemetry exporter."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from threading import Lock
from typing import Protocol


class TelemetryRecorder(Protocol):
    """Minimal metrics boundary without coupling the foundation to an exporter."""

    def record_http(
        self, route: str, method: str, status_code: int, duration_ms: float
    ) -> None: ...

    def record_readiness(self, status: str) -> None: ...

    def record_dependency(self, dependency_id: str, status: str, duration_ms: float) -> None: ...


@dataclass(frozen=True, slots=True)
class TelemetrySnapshot:
    http_requests: dict[str, int]
    http_failures: dict[str, int]
    readiness: dict[str, int]
    dependency_checks: dict[str, int]
    durations_ms: dict[str, tuple[float, ...]]


class InMemoryTelemetry:
    """Thread-safe test/local recorder with only bounded labels."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._http_requests: Counter[str] = Counter()
        self._http_failures: Counter[str] = Counter()
        self._readiness: Counter[str] = Counter()
        self._dependency_checks: Counter[str] = Counter()
        self._durations: dict[str, list[float]] = defaultdict(list)

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

    def snapshot(self) -> TelemetrySnapshot:
        with self._lock:
            return TelemetrySnapshot(
                http_requests=dict(self._http_requests),
                http_failures=dict(self._http_failures),
                readiness=dict(self._readiness),
                dependency_checks=dict(self._dependency_checks),
                durations_ms={key: tuple(values) for key, values in self._durations.items()},
            )


class NoopTelemetry:
    """Production-safe no-op until an exporter is configured."""

    def record_http(self, route: str, method: str, status_code: int, duration_ms: float) -> None:
        return None

    def record_readiness(self, status: str) -> None:
        return None

    def record_dependency(self, dependency_id: str, status: str, duration_ms: float) -> None:
        return None
