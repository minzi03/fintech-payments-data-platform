"""Explicit Arrow schema and exact-value Parquet round-trip tests."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pyarrow.parquet as pq

from common.storage import LocalStorageBackend
from ingestion.cdc_consumer.inspection import parquet_summary
from ingestion.cdc_consumer.models import build_batch, deterministic_json
from ingestion.cdc_consumer.parquet import (
    CDC_ARROW_SCHEMA,
    cleanup_serialized,
    serialize_batch,
)
from ingestion.cdc_consumer.storage import CdcObjectStorage
from tests.unit.ingestion.cdc_consumer import make_event


def test_arrow_schema_is_explicit_and_coordinates_are_integer_widths() -> None:
    assert CDC_ARROW_SCHEMA.field("kafka_offset").type.bit_width == 64
    assert CDC_ARROW_SCHEMA.field("kafka_partition").type.bit_width == 32
    assert str(CDC_ARROW_SCHEMA.field("ingested_at").type) == "timestamp[us, tz=UTC]"
    assert CDC_ARROW_SCHEMA.names[-1] == "raw_event_json"


def test_decimal_json_is_string_preserving_not_float() -> None:
    encoded = deterministic_json({"amount": Decimal("9999999999999999.99")})
    assert encoded == '{"amount":"9999999999999999.99"}'
    assert "e+" not in encoded.lower()


def test_parquet_round_trip_and_temp_cleanup(tmp_path) -> None:
    batch = build_batch((make_event(10), make_event(11, operation="u")))
    ingested_at = datetime(2026, 7, 22, 2, 3, 4, tzinfo=UTC)
    serialized = serialize_batch(batch, temp_dir=tmp_path, ingested_at=ingested_at)
    try:
        table = pq.read_table(serialized.path)
        assert table.schema.names == CDC_ARROW_SCHEMA.names
        assert table.num_rows == 2
        assert table.column("kafka_offset").to_pylist() == [10, 11]
        assert table.column("after_json").to_pylist()[0] == '{"amount":"123.45"}'
        assert all(value.tzinfo is not None for value in table.column("ingested_at").to_pylist())
        assert len(serialized.checksum_sha256) == 64
    finally:
        cleanup_serialized(serialized)
    assert not serialized.path.exists()


def test_same_manifest_timestamp_produces_same_parquet_checksum(tmp_path) -> None:
    batch = build_batch((make_event(20),))
    timestamp = datetime(2026, 7, 22, tzinfo=UTC)
    first = serialize_batch(batch, temp_dir=tmp_path, ingested_at=timestamp)
    second = serialize_batch(batch, temp_dir=tmp_path, ingested_at=timestamp)
    try:
        assert first.checksum_sha256 == second.checksum_sha256
        assert first.path.read_bytes() == second.path.read_bytes()
    finally:
        cleanup_serialized(first)
        cleanup_serialized(second)


def test_payload_safe_parquet_inspection(tmp_path) -> None:
    batch = build_batch((make_event(30), make_event(31, operation="d")))
    timestamp = datetime(2026, 7, 22, tzinfo=UTC)
    serialized = serialize_batch(batch, temp_dir=tmp_path / "temp", ingested_at=timestamp)
    storage = CdcObjectStorage(
        LocalStorageBackend(
            {
                "fintech-bronze": tmp_path / "bronze",
                "fintech-quarantine": tmp_path / "quarantine",
            }
        ),
        bronze_bucket="fintech-bronze",
        quarantine_bucket="fintech-quarantine",
    )
    try:
        stored = storage.put_batch(
            batch, serialized, consumer_group="consumer-a", ingested_at=timestamp
        )
        summary = parquet_summary(storage, stored.uri)
        assert summary["row_count"] == 2
        assert summary["checksum_sha256"] == serialized.checksum_sha256
        assert summary["operation_counts"] == {"c": 1, "d": 1}
        assert "after_json" in summary["columns"]
        assert "123.45" not in str(summary)
    finally:
        cleanup_serialized(serialized)
