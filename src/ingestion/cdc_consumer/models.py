"""Typed CDC consumer records, identities, and manifest state."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any


class BatchStatus(StrEnum):
    """Persisted lifecycle for one topic-partition offset range."""

    COLLECTING = "COLLECTING"
    SERIALIZING = "SERIALIZING"
    UPLOADING = "UPLOADING"
    UPLOADED = "UPLOADED"
    COMMITTED = "COMMITTED"
    FAILED = "FAILED"


class ParseKind(StrEnum):
    EVENT = "EVENT"
    HEARTBEAT = "HEARTBEAT"
    POISON = "POISON"


@dataclass(frozen=True, slots=True, order=True)
class TopicPartitionKey:
    topic: str
    partition: int


@dataclass(frozen=True, slots=True)
class RawKafkaMessage:
    """Kafka bytes and coordinates before Debezium interpretation."""

    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes | None
    timestamp_ms: int | None

    @property
    def topic_partition(self) -> TopicPartitionKey:
        return TopicPartitionKey(self.topic, self.partition)


@dataclass(frozen=True, slots=True)
class BronzeEvent:
    """Explicit Phase 5 Bronze record independent of Arrow implementation details."""

    event_id: str
    entity_name: str
    operation: str
    is_snapshot: bool
    is_deleted: bool
    is_tombstone: bool
    event_key_json: str | None
    before_json: str | None
    after_json: str | None
    source_metadata_json: str | None
    source_lsn: int | None
    source_tx_id: int | None
    source_ts_ms: int | None
    connector_ts_ms: int | None
    kafka_topic: str
    kafka_partition: int
    kafka_offset: int
    kafka_message_ts_ms: int | None
    ingested_at: datetime
    event_date: str
    schema_version: str
    raw_event_json: str | None

    @property
    def topic_partition(self) -> TopicPartitionKey:
        return TopicPartitionKey(self.kafka_topic, self.kafka_partition)


@dataclass(frozen=True, slots=True)
class PoisonEvent:
    """Confidential evidence that must be durably quarantined before source commit."""

    event_id: str
    error_code: str
    error_message: str
    original_topic: str
    original_partition: int
    original_offset: int
    original_key: str | None
    raw_value: str | None
    failed_at: datetime
    retry_count: int = 0
    encoding: str = "base64"

    def to_dict(self, consumer_group: str) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "original_topic": self.original_topic,
            "original_partition": self.original_partition,
            "original_offset": self.original_offset,
            "original_key": self.original_key,
            "raw_value": self.raw_value,
            "value_encoding": self.encoding,
            "failed_at": utc_isoformat(self.failed_at),
            "consumer_group": consumer_group,
            "retry_count": self.retry_count,
        }


@dataclass(frozen=True, slots=True)
class ParsedMessage:
    kind: ParseKind
    raw: RawKafkaMessage
    event: BronzeEvent | None = None
    poison: PoisonEvent | None = None


@dataclass(frozen=True, slots=True)
class CdcBatch:
    """One contiguous immutable range from exactly one Kafka topic-partition."""

    batch_id: str
    topic: str
    partition: int
    offset_start: int
    offset_end: int
    entity_name: str
    event_date: str
    schema_version: str
    events: tuple[BronzeEvent, ...]

    @property
    def next_offset(self) -> int:
        return self.offset_end + 1

    @property
    def record_count(self) -> int:
        return len(self.events)

    @property
    def contains_snapshot(self) -> bool:
        return any(event.is_snapshot for event in self.events)

    @property
    def contains_delete(self) -> bool:
        return any(event.is_deleted and not event.is_tombstone for event in self.events)

    @property
    def contains_tombstone(self) -> bool:
        return any(event.is_tombstone for event in self.events)


@dataclass(frozen=True, slots=True)
class BatchManifestRecord:
    batch_id: str
    consumer_group: str
    topic: str
    partition: int
    offset_start: int
    offset_end: int
    status: BatchStatus
    record_count: int
    schema_version: str
    checksum_sha256: str | None
    object_uri: str | None
    created_at: datetime
    upload_started_at: datetime | None
    uploaded_at: datetime | None
    committed_at: datetime | None
    error_code: str | None
    error_message: str | None
    retry_count: int


@dataclass(frozen=True, slots=True)
class ConsumerRunResult:
    polled_count: int
    event_count: int
    poison_count: int
    heartbeat_count: int
    batch_count: int
    committed_count: int
    dry_run: bool


def deterministic_event_id(topic: str, partition: int, offset: int) -> str:
    return hashlib.sha256(f"{topic}:{partition}:{offset}".encode()).hexdigest()


def deterministic_batch_id(
    topic: str,
    partition: int,
    offset_start: int,
    offset_end: int,
    schema_version: str,
) -> str:
    identity = f"{topic}:{partition}:{offset_start}:{offset_end}:{schema_version}"
    return hashlib.sha256(identity.encode()).hexdigest()


def build_batch(events: tuple[BronzeEvent, ...]) -> CdcBatch:
    if not events:
        raise ValueError("A CDC batch requires at least one event")
    ordered = tuple(sorted(events, key=lambda event: event.kafka_offset))
    first = ordered[0]
    expected_offsets = tuple(range(first.kafka_offset, first.kafka_offset + len(ordered)))
    actual_offsets = tuple(event.kafka_offset for event in ordered)
    if actual_offsets != expected_offsets:
        raise ValueError("CDC batch offsets must form one contiguous range")
    if any(event.topic_partition != first.topic_partition for event in ordered):
        raise ValueError("CDC batch must contain exactly one topic-partition")
    if any(event.entity_name != first.entity_name for event in ordered):
        raise ValueError("CDC batch must contain exactly one entity")
    if any(event.event_date != first.event_date for event in ordered):
        raise ValueError("CDC batch must contain exactly one event date")
    if any(event.schema_version != first.schema_version for event in ordered):
        raise ValueError("CDC batch must contain exactly one schema version")
    return CdcBatch(
        batch_id=deterministic_batch_id(
            first.kafka_topic,
            first.kafka_partition,
            ordered[0].kafka_offset,
            ordered[-1].kafka_offset,
            first.schema_version,
        ),
        topic=first.kafka_topic,
        partition=first.kafka_partition,
        offset_start=ordered[0].kafka_offset,
        offset_end=ordered[-1].kafka_offset,
        entity_name=first.entity_name,
        event_date=first.event_date,
        schema_version=first.schema_version,
        events=ordered,
    )


def deterministic_json(value: object) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_json_default,
    )


def bytes_as_base64(value: bytes | None) -> str | None:
    return None if value is None else base64.b64encode(value).decode("ascii")


def utc_isoformat(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _json_default(value: Any) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return utc_isoformat(value)
    if isinstance(value, bytes):
        return bytes_as_base64(value)
    raise TypeError(f"Unsupported deterministic JSON value: {type(value).__name__}")
