"""Debezium wrapper, operation, timestamp, and poison parsing tests."""

from __future__ import annotations

from datetime import UTC

import pytest

from ingestion.cdc_consumer.envelope import parse_debezium_message
from ingestion.cdc_consumer.models import ParseKind, deterministic_event_id
from tests.unit.ingestion.cdc_consumer import TOPIC, make_message


@pytest.mark.parametrize("wrapper", [True, False])
@pytest.mark.parametrize("operation", ["r", "c", "u", "d"])
def test_supported_envelopes_and_operations(wrapper: bool, operation: str) -> None:
    parsed = parse_debezium_message(
        make_message(12, operation=operation, wrapper=wrapper),
        schema_version="cdc-bronze-v1",
    )
    assert parsed.kind is ParseKind.EVENT
    assert parsed.event is not None
    assert parsed.event.operation == operation
    assert parsed.event.is_snapshot is (operation == "r")
    assert parsed.event.is_deleted is (operation == "d")
    assert parsed.event.event_id == deterministic_event_id(TOPIC, 0, 12)
    assert parsed.event.ingested_at.tzinfo is not None
    assert parsed.event.ingested_at.utcoffset() == UTC.utcoffset(None)
    assert parsed.event.source_lsn == 112


def test_delete_retains_before_and_not_after() -> None:
    parsed = parse_debezium_message(make_message(4, operation="d"), schema_version="cdc-bronze-v1")
    assert parsed.event is not None
    assert parsed.event.before_json is not None
    assert parsed.event.after_json is None


def test_null_value_is_an_explicit_tombstone() -> None:
    parsed = parse_debezium_message(make_message(5, value=None), schema_version="cdc-bronze-v1")
    assert parsed.kind is ParseKind.EVENT
    assert parsed.event is not None
    assert parsed.event.operation == "t"
    assert parsed.event.is_tombstone
    assert parsed.event.raw_event_json is None


@pytest.mark.parametrize(
    ("value", "error_code"),
    [
        (b"not-json", "MALFORMED_JSON"),
        (b"[]", "INVALID_ENVELOPE"),
        (b'{"payload":null}', "MISSING_PAYLOAD"),
        (b'{"payload":{"op":"x"}}', "UNSUPPORTED_OPERATION"),
        (b'{"payload":{"op":"c","after":{}}}', "MISSING_SOURCE"),
        (
            b'{"payload":{"op":"c","after":{},"source":{"table":"customers","lsn":"bad"}}}',
            "INVALID_SOURCE_METADATA",
        ),
    ],
)
def test_poison_messages_are_classified_without_logging_payload(
    value: bytes, error_code: str
) -> None:
    parsed = parse_debezium_message(make_message(7, value=value), schema_version="cdc-bronze-v1")
    assert parsed.kind is ParseKind.POISON
    assert parsed.poison is not None
    assert parsed.poison.error_code == error_code
    assert parsed.poison.raw_value is not None


def test_heartbeat_is_distinct_from_business_event() -> None:
    parsed = parse_debezium_message(
        make_message(8, value=b'{"payload":{"ts_ms":1753158600000}}'),
        schema_version="cdc-bronze-v1",
    )
    assert parsed.kind is ParseKind.HEARTBEAT
