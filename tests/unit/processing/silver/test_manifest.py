"""Silver processing manifest lifecycle and idempotency tests."""

from datetime import UTC, datetime

import pytest

from processing.silver.manifest import ProcessingManifestError, SqliteProcessingManifest
from processing.silver.models import OutputType, ProcessingStatus, SilverOutput, SourceType

NOW = datetime(2026, 7, 22, tzinfo=UTC)


def _register(manifest: SqliteProcessingManifest, run_id: str = "run-1") -> None:
    manifest.register(
        run_id=run_id,
        pipeline_name="cdc-bronze-to-silver",
        source_type=SourceType.CDC,
        entity_name="customers",
        input_object_uri="s3://fintech-bronze/cdc/input.parquet",
        input_checksum="a" * 64,
        code_version="phase6-v1",
        schema_version="silver-v1",
        started_at=NOW,
    )


def test_manifest_requires_full_lifecycle_and_indexes_completed_input(tmp_path) -> None:
    manifest = SqliteProcessingManifest(tmp_path / "manifest.sqlite3")
    _register(manifest)
    manifest.mark_reading("run-1")
    manifest.mark_validating("run-1", 2)
    manifest.mark_transforming("run-1")
    manifest.mark_writing("run-1")
    output = SilverOutput(OutputType.HISTORY, "s3://fintech-silver/a.parquet", "b" * 64, 2)
    completed = manifest.mark_completed(
        "run-1", outputs=(output,), output_record_count=2, rejected_record_count=0, completed_at=NOW
    )

    assert completed.status is ProcessingStatus.COMPLETED
    assert (
        manifest.find_completed(
            pipeline_name="cdc-bronze-to-silver",
            input_checksum="a" * 64,
            code_version="phase6-v1",
            schema_version="silver-v1",
        )
        == completed
    )
    assert manifest.latest_output("customers", OutputType.HISTORY) == output


def test_manifest_rejects_skipped_transition(tmp_path) -> None:
    manifest = SqliteProcessingManifest(tmp_path / "manifest.sqlite3")
    _register(manifest)

    with pytest.raises(ProcessingManifestError, match="Invalid"):
        manifest.mark_writing("run-1")
