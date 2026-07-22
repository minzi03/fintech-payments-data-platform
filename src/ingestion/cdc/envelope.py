"""Safe parsing helpers for schema-enabled Debezium JSON envelopes."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any


class EnvelopeError(ValueError):
    """Raised when an inspected record is not a recognizable Debezium envelope."""


@dataclass(frozen=True, slots=True)
class EnvelopeSummary:
    """Non-PII CDC metadata shown by the inspection command."""

    op: str
    table: str
    snapshot: str | bool | None
    source_lsn: int | None
    source_ts_ms: int | None
    event_ts_ms: int | None
    transaction_id: str | None


def parse_envelope(document: Mapping[str, Any]) -> EnvelopeSummary:
    """Extract operation/source metadata without returning before/after business payloads."""
    payload = document.get("payload")
    if not isinstance(payload, Mapping):
        raise EnvelopeError("CDC JSON record must contain an object payload")
    op = payload.get("op")
    source = payload.get("source")
    if op not in {"r", "c", "u", "d"} or not isinstance(source, Mapping):
        raise EnvelopeError("CDC payload is missing a supported operation or source metadata")
    table = source.get("table")
    if not isinstance(table, str) or not table:
        raise EnvelopeError("CDC source metadata is missing table")
    transaction = payload.get("transaction")
    transaction_id = None
    if isinstance(transaction, Mapping) and transaction.get("id") is not None:
        transaction_id = str(transaction["id"])
    elif source.get("txId") is not None:
        transaction_id = str(source["txId"])
    return EnvelopeSummary(
        op=op,
        table=table,
        snapshot=source.get("snapshot"),
        source_lsn=_optional_int(source.get("lsn")),
        source_ts_ms=_optional_int(source.get("ts_ms")),
        event_ts_ms=_optional_int(payload.get("ts_ms")),
        transaction_id=transaction_id,
    )


def decimal_schema(document: Mapping[str, Any], field_name: str) -> Mapping[str, Any]:
    """Return a field schema from the Debezium `after` structure."""
    schema = document.get("schema")
    if not isinstance(schema, Mapping):
        raise EnvelopeError("CDC JSON record is missing schema metadata")
    after_schema = _named_field(schema, "after")
    return _named_field(after_schema, field_name)


def decode_precise_decimal(encoded: str, scale: int) -> Decimal:
    """Decode Kafka Connect Decimal bytes without binary floating-point conversion."""
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as error:
        raise EnvelopeError("Precise decimal payload is not valid base64") from error
    unscaled = 0 if not raw else int.from_bytes(raw, byteorder="big", signed=True)
    return Decimal(unscaled).scaleb(-scale)


def key_payload(document: Mapping[str, Any] | None) -> dict[str, object] | None:
    """Return the primary-key payload only; never expose the full row."""
    if document is None:
        return None
    payload = document.get("payload")
    if not isinstance(payload, Mapping):
        return None
    return {str(key): value for key, value in payload.items()}


def _named_field(schema: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    fields = schema.get("fields")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes)):
        raise EnvelopeError(f"Schema does not contain fields for {name}")
    for field in fields:
        if isinstance(field, Mapping) and field.get("field") == name:
            return field
    raise EnvelopeError(f"Schema field not found: {name}")


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise EnvelopeError("CDC timestamp/LSN metadata must be an integer") from error
