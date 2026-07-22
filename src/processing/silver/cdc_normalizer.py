"""CDC envelope normalization and typed entity projection without float conversion."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import pyarrow as pa

from ingestion.cdc_consumer.models import deterministic_event_id
from processing.silver.models import (
    BUSINESS_KEYS,
    InputObject,
    NormalizedCdcEvent,
    QualityCode,
    QualityRejection,
    canonical_json,
    utc,
)
from processing.silver.quality import rejection

SUPPORTED_OPERATIONS = frozenset({"r", "c", "u", "d", "t"})
DECIMAL_FIELDS = {
    "accounts": {"balance"},
    "payment_transactions": {"amount"},
    "refunds": {"amount"},
}
TIMESTAMP_FIELDS = {
    "customers": {"created_at", "updated_at"},
    "accounts": {"created_at", "updated_at"},
    "merchants": {"created_at", "updated_at"},
    "payment_transactions": {
        "requested_at",
        "completed_at",
        "failed_at",
        "created_at",
        "updated_at",
    },
    "transaction_events": {"event_time", "producer_time", "created_at"},
    "refunds": {"requested_at", "completed_at", "created_at", "updated_at"},
}


class SilverNormalizationError(ValueError):
    def __init__(self, code: QualityCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def _json_object(value: str | None, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        parsed = json.loads(value, parse_float=Decimal)
    except json.JSONDecodeError as error:
        raise SilverNormalizationError(
            QualityCode.INVALID_JSON, f"{field} is invalid JSON"
        ) from error
    if parsed is not None and not isinstance(parsed, dict):
        raise SilverNormalizationError(QualityCode.INVALID_JSON, f"{field} must be a JSON object")
    return parsed


def _payload_from_key(key: dict[str, Any] | None) -> dict[str, Any]:
    if key is None:
        return {}
    payload = key.get("payload", key)
    return payload if isinstance(payload, dict) else {}


def _epoch_ms(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise SilverNormalizationError(QualityCode.INVALID_TIMESTAMP, "Boolean is not a timestamp")
    try:
        return datetime.fromtimestamp(int(value) / 1_000, tz=UTC)
    except (OSError, OverflowError, TypeError, ValueError) as error:
        raise SilverNormalizationError(
            QualityCode.INVALID_TIMESTAMP, "Epoch milliseconds are invalid"
        ) from error


def normalize_cdc_table(
    table: pa.Table,
    *,
    input_object: InputObject,
    run_id: str,
    processed_at: datetime,
    silver_schema_version: str,
) -> tuple[list[NormalizedCdcEvent], list[QualityRejection]]:
    events: list[NormalizedCdcEvent] = []
    rejections: list[QualityRejection] = []
    processed_at = utc(processed_at)
    for row in table.to_pylist():
        event_id = str(row.get("event_id") or "")
        entity = str(row.get("entity_name") or "unknown")
        operation = str(row.get("operation") or "")
        business_key: str | None = None
        try:
            if entity not in BUSINESS_KEYS:
                raise SilverNormalizationError(
                    QualityCode.INVALID_BRONZE_SCHEMA, "CDC entity is unsupported"
                )
            if operation not in SUPPORTED_OPERATIONS:
                raise SilverNormalizationError(
                    QualityCode.UNSUPPORTED_OPERATION, "CDC operation is unsupported"
                )
            topic = str(row["kafka_topic"])
            partition = int(row["kafka_partition"])
            offset = int(row["kafka_offset"])
            if event_id != deterministic_event_id(topic, partition, offset):
                raise SilverNormalizationError(
                    QualityCode.DUPLICATE_EVENT, "event_id does not match Kafka coordinates"
                )
            before = _json_object(row.get("before_json"), "before_json")
            after = _json_object(row.get("after_json"), "after_json")
            key = _payload_from_key(_json_object(row.get("event_key_json"), "event_key_json"))
            _json_object(row.get("source_metadata_json"), "source_metadata_json")
            payload = before if operation == "d" else after
            if operation == "t":
                payload = None
            key_name = BUSINESS_KEYS[entity]
            raw_business_key = (payload or {}).get(key_name, key.get(key_name))
            if raw_business_key in {None, ""}:
                raise SilverNormalizationError(
                    QualityCode.MISSING_BUSINESS_KEY, f"{key_name} is missing"
                )
            business_key = str(raw_business_key)
            source_ts = _epoch_ms(row.get("source_ts_ms"))
            connector_ts = _epoch_ms(row.get("connector_ts_ms"))
            kafka_ts = _epoch_ms(row.get("kafka_message_ts_ms"))
            ingested_at = utc(row["ingested_at"])
            event_time = source_ts or connector_ts or kafka_ts or ingested_at
            events.append(
                NormalizedCdcEvent(
                    event_id=event_id,
                    entity_name=entity,
                    business_key=business_key,
                    operation=operation,
                    is_snapshot=operation == "r",
                    is_deleted=operation in {"d", "t"},
                    is_tombstone=operation == "t",
                    before_json=canonical_json(before) if before is not None else None,
                    after_json=canonical_json(after) if after is not None else None,
                    business_payload_json=canonical_json(payload) if payload is not None else None,
                    source_lsn=int(row["source_lsn"])
                    if row.get("source_lsn") is not None
                    else None,
                    source_tx_id=int(row["source_tx_id"])
                    if row.get("source_tx_id") is not None
                    else None,
                    source_ts=source_ts,
                    connector_ts=connector_ts,
                    kafka_message_ts=kafka_ts,
                    kafka_topic=topic,
                    kafka_partition=partition,
                    kafka_offset=offset,
                    event_time=event_time,
                    ingested_at=ingested_at,
                    processed_at=processed_at,
                    processing_run_id=run_id,
                    source_schema_version=str(row["schema_version"]),
                    silver_schema_version=silver_schema_version,
                )
            )
        except (KeyError, TypeError, ValueError, SilverNormalizationError) as error:
            code = (
                error.code
                if isinstance(error, SilverNormalizationError)
                else QualityCode.INVALID_BRONZE_SCHEMA
            )
            rejections.append(
                rejection(
                    source_object_uri=input_object.uri,
                    source_event_id=event_id or None,
                    entity_name=entity,
                    business_key=business_key,
                    code=code,
                    message=str(error),
                    raw_reference=f"row:event_id={event_id or 'unknown'}",
                    run_id=run_id,
                    rejected_at=processed_at,
                )
            )
    return events, rejections


def decode_decimal(value: object, *, precision: int = 18, scale: int = 2) -> Decimal:
    if isinstance(value, float):
        raise SilverNormalizationError(QualityCode.INVALID_DECIMAL, "Float money is forbidden")
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation:
            try:
                raw = base64.b64decode(value, validate=True)
                if not raw:
                    raise ValueError("empty precise decimal")
                parsed = Decimal(int.from_bytes(raw, byteorder="big", signed=True)).scaleb(-scale)
            except (binascii.Error, ValueError) as error:
                raise SilverNormalizationError(
                    QualityCode.INVALID_DECIMAL, "Precise Decimal encoding is invalid"
                ) from error
    else:
        raise SilverNormalizationError(
            QualityCode.INVALID_DECIMAL, "Money value is not Decimal-compatible"
        )
    if not parsed.is_finite():
        raise SilverNormalizationError(QualityCode.INVALID_DECIMAL, "Money value must be finite")
    quantum = Decimal(1).scaleb(-scale)
    try:
        quantized = parsed.quantize(quantum)
    except InvalidOperation as error:
        raise SilverNormalizationError(
            QualityCode.INVALID_DECIMAL, "Money scale is invalid"
        ) from error
    digits = len(quantized.as_tuple().digits)
    if digits > precision:
        raise SilverNormalizationError(
            QualityCode.INVALID_DECIMAL, "Money precision exceeds NUMERIC(18,2)"
        )
    return quantized


def decode_debezium_timestamp(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return utc(value)
    if isinstance(value, bool):
        raise SilverNormalizationError(QualityCode.INVALID_TIMESTAMP, "Boolean is not a timestamp")
    if isinstance(value, (int, Decimal)):
        integer = int(value)
        divisor = 1_000_000 if abs(integer) >= 100_000_000_000_000 else 1_000
        try:
            return datetime.fromtimestamp(integer / divisor, tz=UTC)
        except (OSError, OverflowError, ValueError) as error:
            raise SilverNormalizationError(
                QualityCode.INVALID_TIMESTAMP, "Debezium timestamp is out of range"
            ) from error
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise SilverNormalizationError(
                QualityCode.INVALID_TIMESTAMP, "Timestamp is not ISO-8601"
            ) from error
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise SilverNormalizationError(
                QualityCode.INVALID_TIMESTAMP, "Timestamp must include timezone"
            )
        return parsed.astimezone(UTC)
    raise SilverNormalizationError(
        QualityCode.INVALID_TIMESTAMP, "Timestamp representation is unsupported"
    )


def project_entity_state(event: NormalizedCdcEvent) -> dict[str, object]:
    if event.business_payload_json is None:
        raise SilverNormalizationError(
            QualityCode.INVALID_BRONZE_SCHEMA, "State event has no business payload"
        )
    payload = _json_object(event.business_payload_json, "business_payload_json") or {}
    row: dict[str, object] = dict(payload)
    if event.entity_name == "customers":
        full_name = str(row.pop("full_name", "")).strip()
        first, separator, last = full_name.partition(" ")
        row["first_name"] = first or None
        row["last_name"] = last if separator else None
    for field in DECIMAL_FIELDS.get(event.entity_name, set()):
        if row.get(field) is not None:
            row[field] = decode_decimal(row[field])
    for field in TIMESTAMP_FIELDS.get(event.entity_name, set()):
        row[field] = decode_debezium_timestamp(row.get(field))
    if event.entity_name == "transaction_events":
        if row.get("event_version") is not None:
            row["event_version"] = int(row["event_version"])
        payload_value = row.pop("event_payload", None)
        row["event_payload_json"] = (
            canonical_json(payload_value) if payload_value is not None else None
        )
    required_references = {
        "accounts": ("customer_id",),
        "payment_transactions": ("customer_id", "account_id"),
        "refunds": ("transaction_id",),
    }
    missing_references = [
        field for field in required_references.get(event.entity_name, ()) if not row.get(field)
    ]
    if missing_references:
        raise SilverNormalizationError(
            QualityCode.INVALID_REFERENCE,
            f"Required reference is missing: {', '.join(missing_references)}",
        )
    row.update(
        {
            "is_deleted": event.is_deleted,
            "source_lsn": event.source_lsn,
            "kafka_topic": event.kafka_topic,
            "kafka_partition": event.kafka_partition,
            "kafka_offset": event.kafka_offset,
            "effective_event_time": event.event_time,
            "processed_at": event.processed_at,
            "processing_run_id": event.processing_run_id,
            "source_schema_version": event.source_schema_version,
            "silver_schema_version": event.silver_schema_version,
        }
    )
    return row


def history_row(event: NormalizedCdcEvent) -> dict[str, object]:
    return {
        field: getattr(event, field)
        for field in (
            "event_id",
            "entity_name",
            "business_key",
            "operation",
            "is_snapshot",
            "is_deleted",
            "is_tombstone",
            "before_json",
            "after_json",
            "business_payload_json",
            "source_lsn",
            "source_tx_id",
            "source_ts",
            "connector_ts",
            "kafka_message_ts",
            "kafka_topic",
            "kafka_partition",
            "kafka_offset",
            "event_time",
            "ingested_at",
            "processed_at",
            "processing_run_id",
            "source_schema_version",
            "silver_schema_version",
        )
    }
