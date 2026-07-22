"""Silver deterministic layout and immutable write tests."""

from datetime import UTC, date, datetime

from common.storage import LocalStorageBackend
from processing.silver.models import OutputType
from processing.silver.parquet import serialize_rows
from processing.silver.schemas import REJECTION_SCHEMA
from processing.silver.storage import SilverStorage, build_silver_object_key


def test_output_keys_keep_run_and_lineage_partitions() -> None:
    key = build_silver_object_key(
        output_type=OutputType.LATEST_ALL,
        entity_name="customers",
        run_id="run-1",
        processing_date=date(2026, 7, 22),
    )

    assert key.startswith(
        "silver/cdc/latest_all/entity=customers/snapshot_date=2026-07-22/run_id=run-1/"
    )
    assert key.endswith(".parquet")


def test_local_silver_write_is_immutable_and_verified(tmp_path) -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    backend = LocalStorageBackend({"fintech-silver": tmp_path / "silver"})
    storage = SilverStorage(backend, bucket="fintech-silver")
    row = {
        "source_object_uri": "local-input",
        "source_event_id": None,
        "entity_name": "customers",
        "business_key": None,
        "error_code": "INVALID_JSON",
        "error_message": "invalid",
        "raw_reference": "row:1",
        "processing_run_id": "run-1",
        "rejected_at": now,
    }
    serialized = serialize_rows([row], schema=REJECTION_SCHEMA, temp_dir=tmp_path, prefix="reject")
    key = build_silver_object_key(
        output_type=OutputType.REJECTIONS,
        entity_name="bronze:customers",
        run_id="run-1",
        processing_date=now.date(),
    )

    first = storage.put(
        serialized=serialized,
        object_key=key,
        output_type=OutputType.REJECTIONS,
        entity_name="customers",
        run_id="run-1",
        input_checksum="a" * 64,
        code_version="phase6-v1",
        source_schema_version="cdc-bronze-v1",
        silver_schema_version="silver-v1",
        processed_at=now,
    )
    second = storage.put(
        serialized=serialized,
        object_key=key,
        output_type=OutputType.REJECTIONS,
        entity_name="customers",
        run_id="run-1",
        input_checksum="a" * 64,
        code_version="phase6-v1",
        source_schema_version="cdc-bronze-v1",
        silver_schema_version="silver-v1",
        processed_at=now,
    )

    assert first.checksum_sha256 == second.checksum_sha256
    assert storage.read_table(first.object_uri).num_rows == 1
