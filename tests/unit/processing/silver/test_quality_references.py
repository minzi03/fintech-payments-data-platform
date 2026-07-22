"""Quality evidence and non-blocking reference classification tests."""

import pyarrow as pa

from ingestion.cdc_consumer.parquet import CDC_ARROW_SCHEMA
from processing.silver.cdc_normalizer import normalize_cdc_table, project_entity_state
from processing.silver.references import classify_unresolved_references

from .conftest import NOW, bronze_row, write_bronze


def test_unresolved_reference_is_classified_without_payload(tmp_path) -> None:
    payload = {
        "account_id": "a-1",
        "customer_id": "late-customer",
        "account_number": "A1",
        "currency": "USD",
        "balance": "10.00",
        "status": "ACTIVE",
        "created_at": 1_753_185_600_000_000,
        "updated_at": 1_753_185_600_000_000,
    }
    row = bronze_row("accounts", "a-1", payload)
    item = write_bronze(tmp_path, [row], entity="accounts")
    events, _ = normalize_cdc_table(
        pa.Table.from_pylist([row], schema=CDC_ARROW_SCHEMA),
        input_object=item,
        run_id="run-1",
        processed_at=NOW,
        silver_schema_version="silver-v1",
    )
    state = project_entity_state(events[0])

    unresolved = classify_unresolved_references(events[0], state, {}, observed_at=NOW)

    assert unresolved[0].classification == "TEMPORARILY_UNRESOLVED"
    assert unresolved[0].reference_entity == "customers"
