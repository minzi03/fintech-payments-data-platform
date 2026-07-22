"""Bounded-retry Kafka Connect REST client with idempotent connector reconciliation."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .config import CdcSettings, ConnectorDefinition

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
MASKED_PASSWORDS = frozenset({"********", "[hidden]"})


class ConnectApiError(RuntimeError):
    """Raised for actionable, redacted Kafka Connect API failures."""


class EnsureAction(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    payload: Any


class HttpTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        payload: Mapping[str, object] | None,
        timeout_seconds: int,
    ) -> HttpResponse: ...


class UrllibJsonTransport:
    """Small JSON transport that performs exactly one serialization per request."""

    def request(
        self,
        method: str,
        url: str,
        payload: Mapping[str, object] | None,
        timeout_seconds: int,
    ) -> HttpResponse:
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            url,
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(response.status, _decode_payload(response.read()))
        except HTTPError as error:
            return HttpResponse(error.code, _decode_payload(error.read()))


class ConnectClient:
    """Kafka Connect operations needed by bootstrap, runbooks, and integration tests."""

    def __init__(
        self,
        settings: CdcSettings,
        *,
        transport: HttpTransport | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._transport = transport or UrllibJsonTransport()
        self._sleeper = sleeper
        self._secrets = (settings.database_password,)

    def wait_ready(self) -> None:
        """Wait until the worker REST endpoint answers successfully."""
        self._request("GET", "/connector-plugins", expected={200})

    def validate(self, definition: ConnectorDefinition) -> None:
        connector_class = definition.config["connector.class"]
        validation_payload = {"name": definition.name, **definition.config}
        response = self._request(
            "PUT",
            f"/connector-plugins/{quote(connector_class, safe='')}/config/validate",
            payload=validation_payload,
            expected={200},
        )
        payload = _mapping(response.payload)
        error_count = int(payload.get("error_count", 0))
        if error_count:
            errors = []
            for config_entry in payload.get("configs", []):
                if not isinstance(config_entry, Mapping):
                    continue
                value = config_entry.get("value", {})
                if isinstance(value, Mapping):
                    errors.extend(str(item) for item in value.get("errors", []) if item)
            detail = "; ".join(errors) or f"{error_count} connector fields are invalid"
            raise ConnectApiError(redact_text(detail, self._secrets))

    def get_config(self, connector_name: str | None = None) -> dict[str, str] | None:
        name = connector_name or self.settings.connector_name
        response = self._request(
            "GET",
            f"/connectors/{quote(name, safe='')}/config",
            expected={200, 404},
        )
        if response.status == 404:
            return None
        payload = _mapping(response.payload)
        return {str(key): str(value) for key, value in payload.items()}

    def ensure(self, definition: ConnectorDefinition) -> EnsureAction:
        """Validate then create, update, or leave an equivalent connector unchanged."""
        self.validate(definition)
        existing = self.get_config(definition.name)
        if existing is None:
            response = self._request(
                "POST",
                "/connectors",
                payload={"name": definition.name, "config": definition.config},
                expected={201, 409},
            )
            if response.status == 201:
                return EnsureAction.CREATED
            existing = self.get_config(definition.name)
            if existing is None:
                raise ConnectApiError("Connector create conflicted but no connector is visible")
        if connector_configs_equal(existing, definition.config):
            return EnsureAction.UNCHANGED
        self._request(
            "PUT",
            f"/connectors/{quote(definition.name, safe='')}/config",
            payload=definition.config,
            expected={200, 201},
        )
        return EnsureAction.UPDATED

    def status(self, connector_name: str | None = None) -> dict[str, Any]:
        name = connector_name or self.settings.connector_name
        response = self._request(
            "GET", f"/connectors/{quote(name, safe='')}/status", expected={200}
        )
        return dict(_mapping(response.payload))

    def wait_running(self, connector_name: str | None = None) -> dict[str, Any]:
        """Poll until connector and its non-empty task set are all RUNNING."""
        last_status: dict[str, Any] = {}
        for attempt in range(1, self.settings.http_max_attempts + 1):
            try:
                last_status = self.status(connector_name)
            except ConnectApiError:
                last_status = {}
            connector = last_status.get("connector", {})
            tasks = last_status.get("tasks", [])
            connector_running = (
                isinstance(connector, Mapping) and connector.get("state") == "RUNNING"
            )
            tasks_running = bool(tasks) and all(
                isinstance(task, Mapping) and task.get("state") == "RUNNING" for task in tasks
            )
            if connector_running and tasks_running:
                return last_status
            if attempt < self.settings.http_max_attempts:
                self._sleeper(min(2 ** (attempt - 1), 5))
        raise ConnectApiError("Connector or task did not reach RUNNING state")

    def restart(self, connector_name: str | None = None) -> None:
        name = connector_name or self.settings.connector_name
        self._request(
            "POST",
            f"/connectors/{quote(name, safe='')}/restart?includeTasks=true&onlyFailed=false",
            expected={202, 204},
        )

    def delete(self, connector_name: str | None = None) -> bool:
        name = connector_name or self.settings.connector_name
        response = self._request(
            "DELETE", f"/connectors/{quote(name, safe='')}", expected={204, 404}
        )
        return response.status == 204

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        expected: set[int],
    ) -> HttpResponse:
        url = f"{self.settings.connect_url}{path}"
        for attempt in range(1, self.settings.http_max_attempts + 1):
            try:
                response = self._transport.request(
                    method, url, payload, self.settings.http_timeout_seconds
                )
            except (URLError, OSError) as error:
                if attempt == self.settings.http_max_attempts:
                    raise ConnectApiError(
                        f"Kafka Connect {method} failed after {attempt} attempts"
                    ) from error
            else:
                if response.status in expected:
                    return response
                if (
                    response.status not in RETRYABLE_STATUS_CODES
                    or attempt == self.settings.http_max_attempts
                ):
                    detail = redact_text(_error_detail(response.payload), self._secrets)
                    raise ConnectApiError(
                        f"Kafka Connect {method} returned HTTP {response.status}: {detail}"
                    )
            self._sleeper(min(2 ** (attempt - 1), 5))
        raise ConnectApiError("Kafka Connect request exhausted retries")  # pragma: no cover


def connector_configs_equal(existing: Mapping[str, str], desired: Mapping[str, str]) -> bool:
    """Compare desired keys while accepting REST-masked password values."""
    comparable_existing = {key: value for key, value in existing.items() if key != "name"}
    if set(comparable_existing) != set(desired):
        return False
    for key, desired_value in desired.items():
        existing_value = comparable_existing.get(key)
        if key.endswith("password") and existing_value in MASKED_PASSWORDS:
            continue
        if existing_value != desired_value:
            return False
    return True


def redact_text(value: str, secrets: tuple[str, ...]) -> str:
    """Remove configured secrets from errors before logging or display."""
    redacted = value
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def summarize_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return state/task evidence without connector configuration or traces."""
    connector = payload.get("connector", {})
    tasks = payload.get("tasks", [])
    return {
        "name": payload.get("name"),
        "connector_state": connector.get("state") if isinstance(connector, Mapping) else None,
        "worker_id": connector.get("worker_id") if isinstance(connector, Mapping) else None,
        "tasks": [
            {"id": task.get("id"), "state": task.get("state"), "worker_id": task.get("worker_id")}
            for task in tasks
            if isinstance(task, Mapping)
        ],
    }


def _decode_payload(data: bytes) -> Any:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"message": "non-JSON response body"}


def _mapping(value: Any) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ConnectApiError("Kafka Connect returned an unexpected response shape")
    return value


def _error_detail(value: Any) -> str:
    if isinstance(value, Mapping):
        return str(value.get("message") or value.get("error_code") or "request failed")
    return "request failed"
