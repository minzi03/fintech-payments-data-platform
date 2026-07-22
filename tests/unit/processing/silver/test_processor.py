"""CDC/settlement state, idempotency, force, and publication tests."""

from datetime import date
from decimal import Decimal
from pathlib import Path

from common.storage import sha256_file
from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures
from processing.silver.models import InputObject, OutputType, ProcessingStatus

from .conftest import bronze_row, customer_payload, make_processor, write_bronze


def _output(run, output_type: OutputType):
    return next(output for output in run.outputs if output.output_type is output_type)


def test_processor_builds_history_latest_current_and_delete_state(tmp_path: Path) -> None:
    processor, manifest, storage, settings = make_processor(tmp_path, ["run-create", "run-delete"])
    create = write_bronze(
        settings.storage.local_bronze_root,
        [bronze_row("customers", "c-1", customer_payload("c-1"), offset=0)],
        entity="customers",
        name="create.parquet",
    )
    deleted = write_bronze(
        settings.storage.local_bronze_root,
        [
            bronze_row("customers", "c-1", customer_payload("c-1"), operation="d", offset=1),
            bronze_row("customers", "c-1", None, operation="t", offset=2),
        ],
        entity="customers",
        name="delete.parquet",
    )

    first = processor.process_cdc(create)
    second = processor.process_cdc(deleted)
    second_run = manifest.get(second.run_id or "")

    assert first.status is ProcessingStatus.COMPLETED
    assert second_run is not None and second_run.status is ProcessingStatus.COMPLETED
    latest = storage.read_table(_output(second_run, OutputType.LATEST_ALL).object_uri)
    current = storage.read_table(_output(second_run, OutputType.CURRENT).object_uri)
    history = storage.read_table(_output(second_run, OutputType.HISTORY).object_uri)
    assert latest.num_rows == 1 and latest["is_deleted"].to_pylist() == [True]
    assert latest["kafka_offset"].to_pylist() == [2]
    assert current.num_rows == 0
    assert history["operation"].to_pylist() == ["d", "t"]


def test_same_input_skips_and_force_creates_new_lineage(tmp_path: Path) -> None:
    processor, manifest, storage, settings = make_processor(tmp_path, ["run-1", "run-2"])
    item = write_bronze(
        settings.storage.local_bronze_root,
        [bronze_row("customers", "c-1", customer_payload("c-1"))],
        entity="customers",
    )

    first = processor.process_cdc(item)
    skipped = processor.process_cdc(item)
    forced = processor.process_cdc(item, force_reprocess=True)

    assert first.run_id == "run-1"
    assert skipped.skipped and skipped.run_id == "run-1"
    assert forced.run_id == "run-2" and forced.status is ProcessingStatus.COMPLETED
    assert forced.rejected_record_count == 0
    forced_run = manifest.get("run-2")
    assert forced_run is not None
    latest = storage.read_table(_output(forced_run, OutputType.LATEST_ALL).object_uri)
    assert latest["processing_run_id"].to_pylist() == ["run-2"]
    assert len(manifest.list_all()) == 2


def test_failed_output_write_never_marks_completed(tmp_path: Path) -> None:
    processor, manifest, _storage, settings = make_processor(tmp_path, ["run-failed"])
    item = write_bronze(
        settings.storage.local_bronze_root,
        [bronze_row("customers", "c-1", customer_payload("c-1"))],
        entity="customers",
    )
    original = processor.storage.put
    calls = 0

    def fail_second(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected partial write")
        return original(**kwargs)

    processor.storage.put = fail_second  # type: ignore[method-assign]

    result = processor.process_cdc(item)
    retry_without_force = processor.process_cdc(item)

    assert result.status is ProcessingStatus.FAILED
    assert retry_without_force.skipped
    assert retry_without_force.run_id == "run-failed"
    assert retry_without_force.status is ProcessingStatus.FAILED
    assert manifest.get("run-failed").status is ProcessingStatus.FAILED  # type: ignore[union-attr]


def _settlement_input(source: Path, bronze_root: Path) -> InputObject:
    key = (
        "settlements/partner_id=VCB/settlement_date=2026-07-22/"
        f"ingestion_date=2026-07-22/{source.name}"
    )
    target = bronze_root / Path(*key.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())
    return InputObject(
        uri=str(target),
        bucket="fintech-bronze",
        object_key=key,
        checksum_sha256=sha256_file(target),
        size_bytes=target.stat().st_size,
        metadata={
            "partner_id": "VCB",
            "source_file_name": source.name,
            "ingestion_run_id": "ingest-unit",
            "schema_version": "settlement-v1",
        },
    )


def test_settlement_processor_supports_dry_run_quality_skip_and_force(tmp_path: Path) -> None:
    processor, manifest, storage, settings = make_processor(
        tmp_path,
        ["settlement-valid", "settlement-dry", "settlement-partial", "settlement-force"],
    )
    fixtures = generate_settlement_fixtures(
        FixtureConfig(tmp_path / "fixtures", "VCB", date(2026, 7, 22), 42)
    )
    valid = _settlement_input(fixtures["valid"], settings.storage.local_bronze_root)
    partial = _settlement_input(fixtures["duplicate_rows"], settings.storage.local_bronze_root)

    completed = processor.process_settlement(valid)
    dry_run = processor.process_settlement(partial, dry_run=True)
    partially_accepted = processor.process_settlement(partial)
    skipped = processor.process_settlement(valid)
    forced = processor.process_settlement(valid, force_reprocess=True)

    assert completed.status is ProcessingStatus.COMPLETED
    completed_run = manifest.get(completed.run_id or "")
    assert completed_run is not None
    table = storage.read_table(_output(completed_run, OutputType.SETTLEMENTS).object_uri)
    assert table.num_rows == 5
    assert isinstance(table["amount"].to_pylist()[0], Decimal)
    assert dry_run.dry_run and dry_run.run_id is None
    assert dry_run.rejected_record_count == 1
    partial_run = manifest.get(partially_accepted.run_id or "")
    assert partial_run is not None
    assert _output(partial_run, OutputType.SETTLEMENTS).record_count == 1
    assert _output(partial_run, OutputType.REJECTIONS).record_count == 1
    assert skipped.skipped and skipped.run_id == completed.run_id
    assert forced.status is ProcessingStatus.COMPLETED
    assert forced.run_id == "settlement-force"
