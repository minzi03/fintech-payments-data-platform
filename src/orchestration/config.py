"""Typed, secret-safe Phase 7 configuration."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlsplit
from uuid import UUID

from common.config import ConfigurationError
from orchestration.models import BackfillRequest

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENTITY = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
ALLOWED_CDC_ENTITIES = frozenset(
    {
        "customers",
        "accounts",
        "merchants",
        "payment_transactions",
        "transaction_events",
        "refunds",
    }
)


def _positive_int(environment: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(environment.get(name, str(default)))
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be greater than zero")
    return value


def _rate(environment: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(environment.get(name, str(default)))
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a decimal rate") from error
    if not 0 <= value <= 1:
        raise ConfigurationError(f"{name} must be between 0 and 1")
    return value


@dataclass(frozen=True, slots=True)
class ControlDatabaseSettings:
    connection_uri: str = field(repr=False)

    @classmethod
    def from_env(cls, environment: Mapping[str, str]) -> ControlDatabaseSettings:
        uri = environment.get("AIRFLOW_CONN_CONTROL_DB", "").strip()
        if not uri:
            raise ConfigurationError("AIRFLOW_CONN_CONTROL_DB must be set")
        parsed = urlsplit(uri)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ConfigurationError("AIRFLOW_CONN_CONTROL_DB must be a PostgreSQL URI")
        if not parsed.path.lstrip("/"):
            raise ConfigurationError("AIRFLOW_CONN_CONTROL_DB must include a database")
        return cls(connection_uri=uri)

    @property
    def connection_label(self) -> str:
        parsed = urlsplit(self.connection_uri)
        return f"{parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"


@dataclass(frozen=True, slots=True)
class OrchestrationSettings:
    timezone: str
    settlement_schedule: str
    cdc_health_schedule: str
    silver_schedule: str
    settlement_warn_rate: float
    settlement_fail_rate: float
    silver_warn_rate: float
    silver_fail_rate: float
    cdc_lag_warn: int
    cdc_lag_fail: int
    cdc_freshness_warn_seconds: int
    cdc_freshness_fail_seconds: int
    task_retries: int
    retry_delay_seconds: int
    execution_timeout_seconds: int

    @classmethod
    def from_env(cls, environment: Mapping[str, str]) -> OrchestrationSettings:
        timezone = environment.get("AIRFLOW_TIMEZONE", "UTC").strip()
        if not timezone or len(timezone) > 64:
            raise ConfigurationError("AIRFLOW_TIMEZONE is invalid")
        settlement_warn = _rate(environment, "SETTLEMENT_REJECTION_WARN_RATE", 0.01)
        settlement_fail = _rate(environment, "SETTLEMENT_REJECTION_FAIL_RATE", 0.05)
        silver_warn = _rate(environment, "SILVER_REJECTION_WARN_RATE", 0.01)
        silver_fail = _rate(environment, "SILVER_REJECTION_FAIL_RATE", 0.05)
        if settlement_warn > settlement_fail:
            raise ConfigurationError("Settlement warn rate must not exceed fail rate")
        if silver_warn > silver_fail:
            raise ConfigurationError("Silver warn rate must not exceed fail rate")
        cdc_lag_warn = _positive_int(environment, "CDC_LAG_WARN_THRESHOLD", 1000)
        cdc_lag_fail = _positive_int(environment, "CDC_LAG_FAIL_THRESHOLD", 10000)
        freshness_warn = _positive_int(environment, "CDC_FRESHNESS_WARN_SECONDS", 120)
        freshness_fail = _positive_int(environment, "CDC_FRESHNESS_FAIL_SECONDS", 600)
        if cdc_lag_warn > cdc_lag_fail:
            raise ConfigurationError("CDC lag warn threshold must not exceed fail threshold")
        if freshness_warn > freshness_fail:
            raise ConfigurationError("CDC freshness warn threshold must not exceed fail threshold")
        retries = int(environment.get("AIRFLOW_TASK_RETRIES", "2"))
        if not 0 <= retries <= 10:
            raise ConfigurationError("AIRFLOW_TASK_RETRIES must be between 0 and 10")
        return cls(
            timezone=timezone,
            settlement_schedule=environment.get("AIRFLOW_SETTLEMENT_SCHEDULE", "0 1 * * *"),
            cdc_health_schedule=environment.get("AIRFLOW_CDC_HEALTH_SCHEDULE", "*/5 * * * *"),
            silver_schedule=environment.get("AIRFLOW_SILVER_SCHEDULE", "*/15 * * * *"),
            settlement_warn_rate=settlement_warn,
            settlement_fail_rate=settlement_fail,
            silver_warn_rate=silver_warn,
            silver_fail_rate=silver_fail,
            cdc_lag_warn=cdc_lag_warn,
            cdc_lag_fail=cdc_lag_fail,
            cdc_freshness_warn_seconds=freshness_warn,
            cdc_freshness_fail_seconds=freshness_fail,
            task_retries=retries,
            retry_delay_seconds=_positive_int(environment, "AIRFLOW_RETRY_DELAY_SECONDS", 60),
            execution_timeout_seconds=_positive_int(
                environment, "AIRFLOW_TASK_TIMEOUT_SECONDS", 1800
            ),
        )


def validate_backfill_params(params: Mapping[str, object]) -> BackfillRequest:
    try:
        request_id = UUID(str(params.get("request_id", "")))
    except ValueError as error:
        raise ValueError("request_id must be a valid UUID") from error
    source_type = str(params.get("source_type", "")).upper()
    if source_type not in {"CDC", "SETTLEMENT"}:
        raise ValueError("source_type must be CDC or SETTLEMENT")
    entity_value = params.get("entity")
    entity = str(entity_value) if entity_value else None
    if entity and (_ENTITY.fullmatch(entity) is None or entity not in ALLOWED_CDC_ENTITIES):
        raise ValueError("entity is not an allowed CDC entity")
    if source_type == "SETTLEMENT" and entity:
        raise ValueError("Settlement backfill does not accept a CDC entity")
    prefix_value = params.get("input_prefix")
    prefix = str(prefix_value) if prefix_value else None
    if prefix and (prefix.startswith(("/", "\\")) or ".." in prefix.split("/")):
        raise ValueError("input_prefix must be a relative object prefix")
    from_date = _optional_date(params.get("from_date"), "from_date")
    to_date = _optional_date(params.get("to_date"), "to_date")
    if from_date and to_date and from_date > to_date:
        raise ValueError("from_date must not be after to_date")
    return BackfillRequest(
        request_id=request_id,
        source_type=source_type,
        entity_name=entity,
        input_prefix=prefix,
        from_date=from_date,
        to_date=to_date,
        force_reprocess=bool(params.get("force_reprocess", False)),
        dry_run=bool(params.get("dry_run", True)),
    )


def _optional_date(value: object, name: str) -> date | None:
    if value in {None, ""}:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError as error:
        raise ValueError(f"{name} must use YYYY-MM-DD") from error
