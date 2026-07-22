"""Debezium JSON parsing without leaking business payloads to logs."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from ingestion.cdc_consumer.models import (
    BronzeEvent,
    ParsedMessage,
    ParseKind,
    PoisonEvent,
    RawKafkaMessage,
    bytes_as_base64,
    deterministic_event_id,
    deterministic_json,
)

SUPPORTED_OPERATIONS = frozenset({"r", "c", "u", "d"})


def _decode_json(value: bytes) -> tuple[Any, str]:
    text = value.decode("utf-8")
    return json.loads(text, parse_float=Decimal), text


def _key_json(key: bytes | None) -> str | None:
    if key is None:
        return None
    try:
        decoded, _ = _decode_json(key)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return deterministic_json({"base64": bytes_as_base64(key)})
    return deterministic_json(decoded)


def _entity_from_topic(topic: str) -> str:
    entity = topic.rsplit(".", maxsplit=1)[-1]
    return entity if entity else "unknown"


def _event_date(*timestamps_ms: int | None) -> str:
    selected = next((value for value in timestamps_ms if value is not None), None)
    if selected is None:
        return datetime.now(UTC).date().isoformat()
    return datetime.fromtimestamp(selected / 1_000, tz=UTC).date().isoformat()


def _poison(
    message: RawKafkaMessage,
    error_code: str,
    error_message: str,
    *,
    retry_count: int = 0,
) -> ParsedMessage:
    return ParsedMessage(
        kind=ParseKind.POISON,
        raw=message,
        poison=PoisonEvent(
            event_id=deterministic_event_id(message.topic, message.partition, message.offset),
            error_code=error_code,
            error_message=error_message,
            original_topic=message.topic,
            original_partition=message.partition,
            original_offset=message.offset,
            original_key=bytes_as_base64(message.key),
            raw_value=bytes_as_base64(message.value),
            failed_at=(
                datetime.fromtimestamp(message.timestamp_ms / 1_000, tz=UTC)
                if message.timestamp_ms is not None
                else datetime(1970, 1, 1, tzinfo=UTC)
            ),
            retry_count=retry_count,
        ),
    )


def parse_debezium_message(
    message: RawKafkaMessage,
    *,
    schema_version: str,
    ingested_at: datetime | None = None,
) -> ParsedMessage:
    """Parse one Kafka record into the explicit Bronze event model.

    The parser accepts both Debezium's schema wrapper and a directly emitted
    payload. JSON decimal tokens are decoded as ``Decimal`` rather than float.
    """

    observed_at = ingested_at or datetime.now(UTC)
    entity = _entity_from_topic(message.topic)
    event_id = deterministic_event_id(message.topic, message.partition, message.offset)

    if message.value is None:
        event = BronzeEvent(
            event_id=event_id,
            entity_name=entity,
            operation="t",
            is_snapshot=False,
            is_deleted=True,
            is_tombstone=True,
            event_key_json=_key_json(message.key),
            before_json=None,
            after_json=None,
            source_metadata_json=None,
            source_lsn=None,
            source_tx_id=None,
            source_ts_ms=None,
            connector_ts_ms=None,
            kafka_topic=message.topic,
            kafka_partition=message.partition,
            kafka_offset=message.offset,
            kafka_message_ts_ms=message.timestamp_ms,
            ingested_at=observed_at,
            schema_version=schema_version,
            raw_event_json=None,
            event_date=_event_date(message.timestamp_ms),
        )
        return ParsedMessage(kind=ParseKind.EVENT, raw=message, event=event)

    try:
        document, raw_text = _decode_json(message.value)
    except UnicodeDecodeError:
        return _poison(message, "MALFORMED_ENCODING", "Value is not valid UTF-8")
    except json.JSONDecodeError:
        return _poison(message, "MALFORMED_JSON", "Value is not valid JSON")

    if not isinstance(document, dict):
        return _poison(message, "INVALID_ENVELOPE", "CDC value must be a JSON object")

    payload = document.get("payload", document)
    if payload is None or not isinstance(payload, dict):
        return _poison(message, "MISSING_PAYLOAD", "Debezium payload is missing")

    operation = payload.get("op")
    if operation is None and "ts_ms" in payload:
        return ParsedMessage(kind=ParseKind.HEARTBEAT, raw=message)
    if operation not in SUPPORTED_OPERATIONS:
        return _poison(
            message,
            "UNSUPPORTED_OPERATION",
            "Debezium operation is absent or unsupported",
        )

    source = payload.get("source")
    if not isinstance(source, dict):
        return _poison(message, "MISSING_SOURCE", "Debezium source metadata is missing")
    if source.get("table") is None or source.get("lsn") is None:
        return _poison(
            message,
            "MISSING_SOURCE_COORDINATES",
            "Debezium source table or LSN is missing",
        )

    before = payload.get("before")
    after = payload.get("after")
    if operation == "d" and before is None:
        return _poison(message, "INVALID_DELETE", "Delete event must retain before state")
    if operation in {"r", "c", "u"} and after is None:
        return _poison(message, "INVALID_AFTER", "Non-delete event must contain after state")

    entity = str(source["table"])
    try:
        source_lsn = _optional_int(source.get("lsn"))
        source_tx_id = _optional_int(source.get("txId"))
        source_ts_ms = _optional_int(source.get("ts_ms"))
        connector_ts_ms = _optional_int(payload.get("ts_ms"))
    except ValueError:
        return _poison(
            message,
            "INVALID_SOURCE_METADATA",
            "Debezium source coordinate or timestamp is invalid",
        )
    event = BronzeEvent(
        event_id=event_id,
        entity_name=entity,
        operation=str(operation),
        is_snapshot=operation == "r",
        is_deleted=operation == "d",
        is_tombstone=False,
        event_key_json=_key_json(message.key),
        before_json=deterministic_json(before) if before is not None else None,
        after_json=deterministic_json(after) if after is not None else None,
        source_metadata_json=deterministic_json(source),
        source_lsn=source_lsn,
        source_tx_id=source_tx_id,
        source_ts_ms=source_ts_ms,
        connector_ts_ms=connector_ts_ms,
        kafka_topic=message.topic,
        kafka_partition=message.partition,
        kafka_offset=message.offset,
        kafka_message_ts_ms=message.timestamp_ms,
        ingested_at=observed_at,
        schema_version=schema_version,
        raw_event_json=raw_text,
        event_date=_event_date(source_ts_ms, connector_ts_ms, message.timestamp_ms),
    )
    return ParsedMessage(kind=ParseKind.EVENT, raw=message, event=event)


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a valid integer timestamp or coordinate")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid integer timestamp or source coordinate") from exc
