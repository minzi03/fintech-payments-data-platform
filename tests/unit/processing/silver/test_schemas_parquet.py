"""Explicit Silver Arrow schema and Decimal round-trip tests."""

from datetime import UTC, datetime
from decimal import Decimal

import pyarrow.parquet as pq

from processing.silver.parquet import serialize_rows
from processing.silver.schemas import ENTITY_SCHEMAS, MONEY, entity_schema


def test_all_six_entity_schemas_are_explicit() -> None:
    assert set(ENTITY_SCHEMAS) == {
        "customers",
        "accounts",
        "merchants",
        "payment_transactions",
        "transaction_events",
        "refunds",
    }
    assert entity_schema("accounts").field("balance").type == MONEY
    assert entity_schema("payment_transactions").field("amount").type == MONEY


def test_decimal_and_utc_round_trip_in_parquet(tmp_path) -> None:
    timestamp = datetime(2026, 7, 22, tzinfo=UTC)
    row = {
        "account_id": "a-1",
        "customer_id": "c-1",
        "account_number": "1",
        "currency": "USD",
        "balance": Decimal("123.45"),
        "status": "ACTIVE",
        "created_at": timestamp,
        "updated_at": timestamp,
        "is_deleted": False,
        "source_lsn": 10,
        "kafka_topic": "fintech.cdc.payments.accounts",
        "kafka_partition": 0,
        "kafka_offset": 1,
        "effective_event_time": timestamp,
        "processed_at": timestamp,
        "processing_run_id": "run-1",
        "source_schema_version": "cdc-bronze-v1",
        "silver_schema_version": "silver-v1",
    }
    serialized = serialize_rows(
        [row], schema=entity_schema("accounts"), temp_dir=tmp_path, prefix="account"
    )
    table = pq.read_table(serialized.path)

    assert table["balance"].to_pylist() == [Decimal("123.45")]
    assert table.schema.field("processed_at").type.tz == "UTC"
