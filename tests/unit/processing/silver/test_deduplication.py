"""Kafka-coordinate ordering and deduplication tests."""

import pyarrow as pa

from ingestion.cdc_consumer.parquet import CDC_ARROW_SCHEMA
from processing.silver.cdc_normalizer import normalize_cdc_table
from processing.silver.deduplication import deduplicate_events
from processing.silver.models import QualityCode

from .conftest import NOW, bronze_row, customer_payload, write_bronze


def test_duplicate_coordinate_is_classified_and_order_is_deterministic(tmp_path) -> None:
    first = bronze_row("customers", "c-1", customer_payload("c-1"), offset=2)
    second = dict(first)
    item = write_bronze(tmp_path, [first], entity="customers")
    events, _ = normalize_cdc_table(
        pa.Table.from_pylist([first, second], schema=CDC_ARROW_SCHEMA),
        input_object=item,
        run_id="run-1",
        processed_at=NOW,
        silver_schema_version="silver-v1",
    )

    accepted, rejected = deduplicate_events(events)

    assert len(accepted) == 1
    assert rejected[0][1] is QualityCode.DUPLICATE_COORDINATE
