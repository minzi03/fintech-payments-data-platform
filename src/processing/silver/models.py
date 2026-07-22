"""Typed Silver processing state, lineage, quality, and normalized records."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class ProcessingStatus(StrEnum):
    DISCOVERED = "DISCOVERED"
    READING = "READING"
    VALIDATING = "VALIDATING"
    TRANSFORMING = "TRANSFORMING"
    WRITING = "WRITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    QUARANTINED = "QUARANTINED"


class SourceType(StrEnum):
    CDC = "CDC"
    SETTLEMENT = "SETTLEMENT"


class OutputType(StrEnum):
    HISTORY = "history"
    LATEST_ALL = "latest_all"
    CURRENT = "current"
    EVENTS = "events"
    SETTLEMENTS = "settlements"
    REJECTIONS = "rejections"
    UNRESOLVED_REFERENCES = "unresolved_references"


class QualityCode(StrEnum):
    INVALID_BRONZE_SCHEMA = "INVALID_BRONZE_SCHEMA"
    INVALID_JSON = "INVALID_JSON"
    MISSING_BUSINESS_KEY = "MISSING_BUSINESS_KEY"
    INVALID_DECIMAL = "INVALID_DECIMAL"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    UNSUPPORTED_OPERATION = "UNSUPPORTED_OPERATION"
    DUPLICATE_EVENT = "DUPLICATE_EVENT"
    DUPLICATE_COORDINATE = "DUPLICATE_COORDINATE"
    OUT_OF_ORDER_EVENT = "OUT_OF_ORDER_EVENT"
    INVALID_REFERENCE = "INVALID_REFERENCE"
    SCHEMA_VERSION_UNSUPPORTED = "SCHEMA_VERSION_UNSUPPORTED"


BUSINESS_KEYS = {
    "customers": "customer_id",
    "accounts": "account_id",
    "merchants": "merchant_id",
    "payment_transactions": "transaction_id",
    "transaction_events": "event_id",
    "refunds": "refund_id",
}


@dataclass(frozen=True, slots=True)
class InputObject:
    uri: str
    bucket: str
    object_key: str
    checksum_sha256: str
    size_bytes: int
    metadata: dict[str, str]


@dataclass(frozen=True, slots=True)
class NormalizedCdcEvent:
    event_id: str
    entity_name: str
    business_key: str
    operation: str
    is_snapshot: bool
    is_deleted: bool
    is_tombstone: bool
    before_json: str | None
    after_json: str | None
    business_payload_json: str | None
    source_lsn: int | None
    source_tx_id: int | None
    source_ts: datetime | None
    connector_ts: datetime | None
    kafka_message_ts: datetime | None
    kafka_topic: str
    kafka_partition: int
    kafka_offset: int
    event_time: datetime
    ingested_at: datetime
    processed_at: datetime
    processing_run_id: str
    source_schema_version: str
    silver_schema_version: str

    @property
    def coordinate(self) -> tuple[str, int, int]:
        return self.kafka_topic, self.kafka_partition, self.kafka_offset


@dataclass(frozen=True, slots=True)
class QualityRejection:
    source_object_uri: str
    source_event_id: str | None
    entity_name: str
    business_key: str | None
    error_code: str
    error_message: str
    raw_reference: str
    processing_run_id: str
    rejected_at: datetime


@dataclass(frozen=True, slots=True)
class UnresolvedReference:
    entity_name: str
    business_key: str
    reference_entity: str
    reference_key: str
    reference_field: str
    classification: str
    source_event_id: str
    processing_run_id: str
    observed_at: datetime


@dataclass(frozen=True, slots=True)
class SilverOutput:
    output_type: OutputType
    object_uri: str
    checksum_sha256: str
    record_count: int

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["output_type"] = self.output_type.value
        return payload


@dataclass(frozen=True, slots=True)
class ProcessingRun:
    run_id: str
    pipeline_name: str
    source_type: SourceType
    entity_name: str
    input_object_uri: str
    input_checksum: str
    input_record_count: int
    status: ProcessingStatus
    started_at: datetime
    completed_at: datetime | None
    output_object_uris: tuple[str, ...]
    outputs: tuple[SilverOutput, ...]
    output_record_count: int
    rejected_record_count: int
    error_code: str | None
    error_message: str | None
    code_version: str
    schema_version: str


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    run_id: str | None
    input_object_uri: str
    status: ProcessingStatus
    entity_name: str
    input_record_count: int = 0
    output_record_count: int = 0
    rejected_record_count: int = 0
    output_object_uris: tuple[str, ...] = ()
    skipped: bool = False
    dry_run: bool = False
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


def deterministic_part_id(run_id: str, output_type: OutputType, entity_name: str) -> str:
    value = f"{run_id}:{output_type.value}:{entity_name}:0"
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(UTC)


def utc_iso(value: datetime) -> str:
    return utc(value).isoformat().replace("+00:00", "Z")


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
