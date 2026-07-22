"""CDC operation, key, Decimal, and timestamp normalization tests."""

import base64
from datetime import UTC
from decimal import Decimal
from pathlib import Path

import pyarrow as pa

from ingestion.cdc_consumer.parquet import CDC_ARROW_SCHEMA
from processing.silver.cdc_normalizer import (
    decode_debezium_timestamp,
    decode_decimal,
    normalize_cdc_table,
    project_entity_state,
)

from .conftest import NOW, bronze_row, customer_payload, write_bronze


def test_snapshot_create_update_delete_tombstone_keep_distinct_semantics(tmp_path: Path) -> None:
    rows = [
        bronze_row("customers", "c-1", customer_payload("c-1"), operation=operation, offset=index)
        for index, operation in enumerate(("r", "c", "u", "d", "t"))
    ]
    item = write_bronze(tmp_path, rows, entity="customers")

    events, rejected = normalize_cdc_table(
        pa.Table.from_pylist(rows, schema=CDC_ARROW_SCHEMA),
        input_object=item,
        run_id="run-1",
        processed_at=NOW,
        silver_schema_version="silver-v1",
    )

    assert not rejected
    assert [event.operation for event in events] == ["r", "c", "u", "d", "t"]
    assert events[0].is_snapshot
    assert events[3].is_deleted and events[3].business_payload_json
    assert events[4].is_tombstone and events[4].business_payload_json is None


def test_precise_decimal_decodes_base64_without_float() -> None:
    encoded = base64.b64encode((12345).to_bytes(2, "big", signed=True)).decode()

    assert decode_decimal(encoded) == Decimal("123.45")
    assert decode_decimal("123.45") == Decimal("123.45")


def test_entity_projection_splits_name_and_normalizes_utc(tmp_path: Path) -> None:
    row = bronze_row("customers", "c-1", customer_payload("c-1"))
    item = write_bronze(tmp_path, [row], entity="customers")
    events, _ = normalize_cdc_table(
        pa.Table.from_pylist([row], schema=CDC_ARROW_SCHEMA),
        input_object=item,
        run_id="run-1",
        processed_at=NOW,
        silver_schema_version="silver-v1",
    )

    state = project_entity_state(events[0])

    assert state["first_name"] == "Ada"
    assert state["last_name"] == "Lovelace"
    assert state["created_at"].tzinfo is UTC
    assert decode_debezium_timestamp(1_753_185_600_000_000).tzinfo is UTC
