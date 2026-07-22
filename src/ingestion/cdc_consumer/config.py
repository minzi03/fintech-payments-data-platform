"""Typed and bounded configuration for reliable CDC Bronze ingestion."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from common.config import ConfigurationError
from ingestion.cdc.config import CAPTURED_TABLES

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_TOPIC = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,248}$")
SAFE_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")


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
    raw = environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as error:
        raise ConfigurationError(f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
    return value


def _positive_float(environ: Mapping[str, str], name: str, default: float) -> float:
    try:
        value = float(environ.get(name, str(default)).strip())
    except ValueError as error:
        raise ConfigurationError(f"{name} must be a positive number") from error
    if value <= 0:
        raise ConfigurationError(f"{name} must be a positive number")
    return value


def _safe_name(value: str, name: str) -> str:
    if SAFE_NAME.fullmatch(value) is None:
        raise ConfigurationError(f"{name} contains unsafe characters")
    return value


def _safe_bucket(value: str, name: str) -> str:
    if SAFE_BUCKET.fullmatch(value) is None:
        raise ConfigurationError(f"{name} must be a valid private S3 bucket name")
    return value


def _parse_topics(value: str, allowed: frozenset[str]) -> tuple[str, ...]:
    topics = tuple(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    if not topics:
        raise ConfigurationError("CDC_CONSUMER_TOPICS must contain explicit topic names")
    if any(SAFE_TOPIC.fullmatch(topic) is None for topic in topics):
        raise ConfigurationError("CDC_CONSUMER_TOPICS contains an invalid or wildcard topic")
    unsupported = sorted(set(topics) - allowed)
    if unsupported:
        raise ConfigurationError(
            f"CDC_CONSUMER_TOPICS contains topics outside the allowlist: {', '.join(unsupported)}"
        )
    return topics


@dataclass(frozen=True, slots=True)
class CdcConsumerSettings:
    bootstrap_servers: str
    group_id: str
    client_id: str
    topics: tuple[str, ...]
    allowed_topics: frozenset[str]
    auto_offset_reset: str
    batch_size: int
    flush_interval_seconds: float
    poll_timeout_ms: int
    max_poll_interval_ms: int
    session_timeout_ms: int
    heartbeat_interval_ms: int
    max_retries: int
    retry_backoff_seconds: float
    dlq_topic: str
    bronze_bucket: str
    quarantine_bucket: str
    schema_version: str
    manifest_path: Path
    temp_dir: Path
    shutdown_timeout_seconds: int

    @classmethod
    def from_env(cls, environ: Mapping[str, str]) -> CdcConsumerSettings:
        prefix = _required(environ, "KAFKA_TOPIC_PREFIX", "fintech.cdc")
        allowed_topics = frozenset(f"{prefix}.payments.{table}" for table in CAPTURED_TABLES)
        topics_value = _required(
            environ,
            "CDC_CONSUMER_TOPICS",
            ",".join(sorted(allowed_topics)),
        )
        auto_offset_reset = _required(environ, "CDC_CONSUMER_AUTO_OFFSET_RESET", "earliest")
        if auto_offset_reset not in {"earliest", "latest"}:
            raise ConfigurationError("CDC_CONSUMER_AUTO_OFFSET_RESET must be earliest or latest")
        session_timeout_ms = _bounded_int(
            environ,
            "CDC_CONSUMER_SESSION_TIMEOUT_MS",
            45_000,
            minimum=6_000,
            maximum=300_000,
        )
        heartbeat_interval_ms = _bounded_int(
            environ,
            "CDC_CONSUMER_HEARTBEAT_INTERVAL_MS",
            15_000,
            minimum=1_000,
            maximum=100_000,
        )
        if heartbeat_interval_ms >= session_timeout_ms:
            raise ConfigurationError(
                "CDC_CONSUMER_HEARTBEAT_INTERVAL_MS must be less than session timeout"
            )
        settings = cls(
            bootstrap_servers=_required(environ, "KAFKA_BOOTSTRAP_SERVERS"),
            group_id=_safe_name(
                _required(environ, "CDC_CONSUMER_GROUP_ID", "fintech-cdc-bronze-v1"),
                "CDC_CONSUMER_GROUP_ID",
            ),
            client_id=_safe_name(
                _required(environ, "CDC_CONSUMER_CLIENT_ID", "fintech-cdc-bronze-consumer"),
                "CDC_CONSUMER_CLIENT_ID",
            ),
            topics=_parse_topics(topics_value, allowed_topics),
            allowed_topics=allowed_topics,
            auto_offset_reset=auto_offset_reset,
            batch_size=_bounded_int(
                environ, "CDC_CONSUMER_BATCH_SIZE", 100, minimum=1, maximum=100_000
            ),
            flush_interval_seconds=_positive_float(
                environ, "CDC_CONSUMER_FLUSH_INTERVAL_SECONDS", 5.0
            ),
            poll_timeout_ms=_bounded_int(
                environ,
                "CDC_CONSUMER_POLL_TIMEOUT_MS",
                1_000,
                minimum=10,
                maximum=60_000,
            ),
            max_poll_interval_ms=_bounded_int(
                environ,
                "CDC_CONSUMER_MAX_POLL_INTERVAL_MS",
                300_000,
                minimum=10_000,
                maximum=3_600_000,
            ),
            session_timeout_ms=session_timeout_ms,
            heartbeat_interval_ms=heartbeat_interval_ms,
            max_retries=_bounded_int(environ, "CDC_CONSUMER_MAX_RETRIES", 3, minimum=1, maximum=10),
            retry_backoff_seconds=_positive_float(
                environ, "CDC_CONSUMER_RETRY_BACKOFF_SECONDS", 0.5
            ),
            dlq_topic=_safe_name(
                _required(environ, "CDC_DLQ_TOPIC", "fintech.cdc.dlq"),
                "CDC_DLQ_TOPIC",
            ),
            bronze_bucket=_safe_bucket(
                _required(
                    environ,
                    "CDC_BRONZE_BUCKET",
                    environ.get("MINIO_BRONZE_BUCKET", "fintech-bronze"),
                ),
                "CDC_BRONZE_BUCKET",
            ),
            quarantine_bucket=_safe_bucket(
                _required(
                    environ,
                    "CDC_QUARANTINE_BUCKET",
                    environ.get("MINIO_QUARANTINE_BUCKET", "fintech-quarantine"),
                ),
                "CDC_QUARANTINE_BUCKET",
            ),
            schema_version=_safe_name(
                _required(environ, "CDC_SCHEMA_VERSION", "cdc-bronze-v1"),
                "CDC_SCHEMA_VERSION",
            ),
            manifest_path=Path(
                environ.get(
                    "CDC_CONSUMER_MANIFEST_DB",
                    "data/control/cdc_consumer_manifest.sqlite3",
                )
            ),
            temp_dir=Path(environ.get("CDC_CONSUMER_TEMP_DIR", "data/tmp/cdc_consumer")),
            shutdown_timeout_seconds=_bounded_int(
                environ,
                "CDC_CONSUMER_SHUTDOWN_TIMEOUT_SECONDS",
                30,
                minimum=1,
                maximum=600,
            ),
        )
        return settings

    def with_overrides(
        self,
        *,
        topics: Sequence[str] | None = None,
        group_id: str | None = None,
        batch_size: int | None = None,
        flush_interval_seconds: float | None = None,
    ) -> CdcConsumerSettings:
        updated = replace(
            self,
            topics=(
                _parse_topics(",".join(topics), self.allowed_topics)
                if topics is not None
                else self.topics
            ),
            group_id=_safe_name(group_id, "group_id") if group_id is not None else self.group_id,
            batch_size=batch_size if batch_size is not None else self.batch_size,
            flush_interval_seconds=(
                flush_interval_seconds
                if flush_interval_seconds is not None
                else self.flush_interval_seconds
            ),
        )
        if updated.batch_size < 1:
            raise ConfigurationError("batch_size must be greater than zero")
        if updated.flush_interval_seconds <= 0:
            raise ConfigurationError("flush_interval must be greater than zero")
        return updated

    def kafka_config(self) -> dict[str, object]:
        """Return a no-auto-commit configuration suitable for confluent-kafka Consumer."""
        return {
            "bootstrap.servers": self.bootstrap_servers,
            "group.id": self.group_id,
            "client.id": self.client_id,
            "auto.offset.reset": self.auto_offset_reset,
            "enable.auto.commit": False,
            "enable.auto.offset.store": False,
            "max.poll.interval.ms": self.max_poll_interval_ms,
            "session.timeout.ms": self.session_timeout_ms,
            "heartbeat.interval.ms": self.heartbeat_interval_ms,
        }
