"""Tests for metadata-only Debezium envelope and Decimal inspection."""

import base64
import json
import subprocess
from decimal import Decimal

import pytest

from ingestion.cdc.envelope import (
    EnvelopeError,
    decimal_schema,
    decode_precise_decimal,
    parse_envelope,
)
from ingestion.cdc.inspection import (
    build_console_consumer_command,
    consume_topic,
    parse_console_record,
)


def envelope(op: str = "u") -> dict[str, object]:
    return {
        "schema": {
            "type": "struct",
            "fields": [
                {
                    "field": "after",
                    "type": "struct",
                    "fields": [
                        {
                            "field": "amount",
                            "type": "bytes",
                            "name": "org.apache.kafka.connect.data.Decimal",
                            "parameters": {"scale": "2", "connect.decimal.precision": "18"},
                        }
                    ],
                }
            ],
        },
        "payload": {
            "before": None,
            "after": {"transaction_id": "not-returned", "amount": "MDk="},
            "op": op,
            "source": {
                "table": "payment_transactions",
                "snapshot": "false",
                "lsn": 123456,
                "ts_ms": 1720000000000,
            },
            "ts_ms": 1720000000010,
            "transaction": {"id": "tx:42"},
        },
    }


@pytest.mark.parametrize("op", ["r", "c", "u", "d"])
def test_supported_operation_metadata_is_parsed_without_row_payload(op: str) -> None:
    summary = parse_envelope(envelope(op))

    assert summary.op == op
    assert summary.table == "payment_transactions"
    assert summary.source_lsn == 123456
    assert summary.transaction_id == "tx:42"
    assert not hasattr(summary, "after")


def test_precise_decimal_bytes_round_trip_without_float() -> None:
    unscaled = 12_345_678
    raw = unscaled.to_bytes((unscaled.bit_length() + 8) // 8, "big", signed=True)
    encoded = base64.b64encode(raw).decode("ascii")

    value = decode_precise_decimal(encoded, 2)

    assert value == Decimal("123456.78")
    assert isinstance(value, Decimal)
    amount_schema = decimal_schema(envelope(), "amount")
    assert amount_schema["name"] == "org.apache.kafka.connect.data.Decimal"
    assert amount_schema["parameters"]["scale"] == "2"


def test_invalid_envelope_is_rejected() -> None:
    with pytest.raises(EnvelopeError, match="supported operation"):
        parse_envelope({"payload": {"op": "x", "source": {}}})


def test_inspection_command_never_uses_a_durable_consumer_group() -> None:
    command = build_console_consumer_command(
        topic="fintech.cdc.payments.customers",
        max_messages=20,
        timeout_ms=10_000,
        compose_env=".env.example",
    )

    assert "--group" not in command
    assert "--from-beginning" in command
    assert "--max-messages" in command


def test_console_record_returns_key_and_metadata_but_not_customer_payload() -> None:
    key = '{"schema":null,"payload":{"customer_id":"customer-1"}}'
    value = json.dumps(envelope("c"), separators=(",", ":"))
    record = parse_console_record(
        f"Partition:1\tOffset:9\t{key}\t{value}",
        topic="fintech.cdc.payments.payment_transactions",
    )

    assert record is not None
    assert record.key == {"customer_id": "customer-1"}
    assert record.partition == 1
    assert record.offset == 9
    assert record.to_dict()["op"] == "c"
    assert "after" not in record.to_dict()


def test_tombstone_is_distinguished_from_delete_envelope() -> None:
    key = '{"schema":null,"payload":{"customer_id":"customer-1"}}'
    record = parse_console_record(
        f"Partition:0\tOffset:10\t{key}\tnull",
        topic="fintech.cdc.payments.customers",
    )

    assert record is not None
    assert record.tombstone is True
    assert record.envelope is None


def test_consume_topic_uses_injected_bounded_runner() -> None:
    key = '{"schema":null,"payload":{"customer_id":"customer-1"}}'
    value = json.dumps(envelope("u"), separators=(",", ":"))

    def runner(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert args[0][0:2] == ["docker", "compose"]  # type: ignore[index]
        assert kwargs == {"capture_output": True, "text": True, "check": False}
        return subprocess.CompletedProcess(
            args[0], 1, f"Partition:0\tOffset:2\t{key}\t{value}\n", ""
        )

    records = consume_topic(
        topic="fintech.cdc.payments.customers",
        max_messages=1,
        timeout_ms=1_000,
        runner=runner,
    )

    assert len(records) == 1
    assert records[0].offset == 2
