"""Typed Phase 4 CDC configuration and Debezium template rendering."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from common.config import ConfigurationError

CAPTURED_TABLES = (
    "customers",
    "accounts",
    "merchants",
    "payment_transactions",
    "transaction_events",
    "refunds",
)
CAPTURED_QUALIFIED_TABLES = tuple(f"payments.{table}" for table in CAPTURED_TABLES)
ALLOWED_SNAPSHOT_MODES = frozenset({"initial", "no_data", "never", "when_needed"})
PLACEHOLDER = re.compile(r"^\$\{([A-Z][A-Z0-9_]*)\}$")
SAFE_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
SAFE_CONNECTOR_NAME = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")
SAFE_TOPIC_PREFIX = re.compile(r"^[a-z][a-z0-9._-]{0,127}$")


def _required(environ: Mapping[str, str], name: str, default: str | None = None) -> str:
    value = environ.get(name, default or "").strip()
    if not value:
        raise ConfigurationError(f"{name} must not be empty")
    return value


def _bounded_int(
    environ: Mapping[str, str],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw_value = environ.get(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _validate_identifier(value: str, name: str) -> str:
    if SAFE_IDENTIFIER.fullmatch(value) is None:
        raise ConfigurationError(f"{name} must be a lowercase PostgreSQL identifier")
    return value


def _validate_http_url(value: str, name: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(f"{name} must be an HTTP(S) URL")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(f"{name} must not contain credentials, query, or fragment")
    if parsed.path not in {"", "/"}:
        raise ConfigurationError(f"{name} must not contain a path")
    return value.rstrip("/")


@dataclass(frozen=True, slots=True)
class CdcSettings:
    """Connector, source, topic, and HTTP settings with hidden credentials."""

    connect_url: str
    connector_name: str
    database_host: str
    database_port: int
    database_name: str
    database_user: str
    database_password: str = field(repr=False)
    slot_name: str = "fintech_payments_cdc_slot"
    publication_name: str = "fintech_payments_cdc_publication"
    topic_prefix: str = "fintech.cdc"
    default_partitions: int = 3
    retention_ms: int = 604_800_000
    heartbeat_interval_ms: int = 10_000
    snapshot_mode: str = "initial"
    http_timeout_seconds: int = 10
    http_max_attempts: int = 5

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> CdcSettings:
        connector_name = _required(environ, "DEBEZIUM_CONNECTOR_NAME", "payments-postgres-cdc")
        if SAFE_CONNECTOR_NAME.fullmatch(connector_name) is None:
            raise ConfigurationError("DEBEZIUM_CONNECTOR_NAME contains unsafe characters")
        topic_prefix = _required(environ, "KAFKA_TOPIC_PREFIX", "fintech.cdc")
        if SAFE_TOPIC_PREFIX.fullmatch(topic_prefix) is None:
            raise ConfigurationError("KAFKA_TOPIC_PREFIX contains unsafe characters")
        snapshot_mode = _required(environ, "DEBEZIUM_SNAPSHOT_MODE", "initial")
        if snapshot_mode not in ALLOWED_SNAPSHOT_MODES:
            allowed = ", ".join(sorted(ALLOWED_SNAPSHOT_MODES))
            raise ConfigurationError(f"DEBEZIUM_SNAPSHOT_MODE must be one of: {allowed}")

        return cls(
            connect_url=_validate_http_url(
                _required(environ, "KAFKA_CONNECT_URL", "http://localhost:8083"),
                "KAFKA_CONNECT_URL",
            ),
            connector_name=connector_name,
            database_host=_required(environ, "DEBEZIUM_DATABASE_HOST", "postgres"),
            database_port=_bounded_int(
                environ,
                "DEBEZIUM_DATABASE_PORT",
                5432,
                minimum=1,
                maximum=65535,
            ),
            database_name=_required(environ, "DEBEZIUM_DATABASE_NAME", "fintech_payments"),
            database_user=_validate_identifier(
                _required(environ, "DEBEZIUM_DATABASE_USER", "payments_cdc"),
                "DEBEZIUM_DATABASE_USER",
            ),
            database_password=_required(environ, "DEBEZIUM_DATABASE_PASSWORD"),
            slot_name=_validate_identifier(
                _required(environ, "DEBEZIUM_SLOT_NAME", "fintech_payments_cdc_slot"),
                "DEBEZIUM_SLOT_NAME",
            ),
            publication_name=_validate_identifier(
                _required(
                    environ,
                    "DEBEZIUM_PUBLICATION_NAME",
                    "fintech_payments_cdc_publication",
                ),
                "DEBEZIUM_PUBLICATION_NAME",
            ),
            topic_prefix=topic_prefix,
            default_partitions=_bounded_int(
                environ, "KAFKA_DEFAULT_PARTITIONS", 3, minimum=1, maximum=100
            ),
            retention_ms=_bounded_int(
                environ,
                "KAFKA_RETENTION_MS",
                604_800_000,
                minimum=60_000,
                maximum=31_536_000_000,
            ),
            heartbeat_interval_ms=_bounded_int(
                environ,
                "DEBEZIUM_HEARTBEAT_INTERVAL_MS",
                10_000,
                minimum=1_000,
                maximum=3_600_000,
            ),
            snapshot_mode=snapshot_mode,
            http_timeout_seconds=_bounded_int(
                environ, "CDC_HTTP_TIMEOUT_SECONDS", 10, minimum=1, maximum=120
            ),
            http_max_attempts=_bounded_int(
                environ, "CDC_HTTP_MAX_ATTEMPTS", 5, minimum=1, maximum=10
            ),
        )

    @property
    def table_include_list(self) -> str:
        return ",".join(CAPTURED_QUALIFIED_TABLES)

    def topic_name(self, table: str) -> str:
        if table not in CAPTURED_TABLES:
            raise ConfigurationError(f"Unsupported CDC table: {table}")
        return f"{self.topic_prefix}.payments.{table}"

    def template_values(self) -> dict[str, str]:
        return {
            "DEBEZIUM_CONNECTOR_NAME": self.connector_name,
            "DEBEZIUM_DATABASE_HOST": self.database_host,
            "DEBEZIUM_DATABASE_PORT": str(self.database_port),
            "DEBEZIUM_DATABASE_NAME": self.database_name,
            "DEBEZIUM_DATABASE_USER": self.database_user,
            "DEBEZIUM_DATABASE_PASSWORD": self.database_password,
            "DEBEZIUM_SLOT_NAME": self.slot_name,
            "DEBEZIUM_PUBLICATION_NAME": self.publication_name,
            "DEBEZIUM_HEARTBEAT_INTERVAL_MS": str(self.heartbeat_interval_ms),
            "DEBEZIUM_SNAPSHOT_MODE": self.snapshot_mode,
            "KAFKA_TOPIC_PREFIX": self.topic_prefix,
            "KAFKA_DEFAULT_PARTITIONS": str(self.default_partitions),
            "KAFKA_RETENTION_MS": str(self.retention_ms),
        }


@dataclass(frozen=True, slots=True)
class ConnectorDefinition:
    """Rendered connector name and Kafka Connect configuration."""

    name: str
    config: dict[str, str]


def render_connector_definition(path: Path, settings: CdcSettings) -> ConnectorDefinition:
    """Render a versioned JSON template without stringifying the payload twice."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ConfigurationError(f"Unable to load connector template: {path}") from error
    rendered = _render_value(document, settings.template_values())
    if not isinstance(rendered, dict) or not isinstance(rendered.get("config"), dict):
        raise ConfigurationError("Connector template must contain name and config objects")
    name = rendered.get("name")
    if not isinstance(name, str) or name != settings.connector_name:
        raise ConfigurationError("Rendered connector name does not match configuration")
    config = rendered["config"]
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in config.items()):
        raise ConfigurationError("Connector configuration keys and values must be strings")
    return ConnectorDefinition(name=name, config=dict(config))


def _render_value(value: Any, replacements: Mapping[str, str]) -> Any:
    if isinstance(value, dict):
        return {key: _render_value(item, replacements) for key, item in value.items()}
    if isinstance(value, list):
        return [_render_value(item, replacements) for item in value]
    if isinstance(value, str):
        match = PLACEHOLDER.fullmatch(value)
        if match is None:
            if "${" in value:
                raise ConfigurationError("Placeholders must occupy an entire JSON string value")
            return value
        name = match.group(1)
        try:
            return replacements[name]
        except KeyError as error:
            raise ConfigurationError(f"Unknown connector template placeholder: {name}") from error
    return value
