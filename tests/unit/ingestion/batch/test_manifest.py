"""Tests for deterministic SQLite manifest identity and lifecycle rules."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from ingestion.batch.manifest import ManifestError, ManifestStore
from ingestion.batch.models import DuplicateKind, ManifestStatus

NOW = datetime(2026, 7, 23, tzinfo=UTC)


def _register(
    store: ManifestStore,
    *,
    name: str = "settlement_VCB_2026-07-22_001.csv",
    checksum: str = "a" * 64,
):
    return store.register_discovery(
        partner_id="VCB",
        file_name=name,
        file_path=Path("inbound") / name,
        file_size_bytes=100,
        checksum_sha256=checksum,
        schema_version="settlement-v1",
        settlement_date="2026-07-22",
        discovered_at=NOW,
        ingestion_run_id="run-1",
    )


def test_manifest_status_lifecycle_and_persistence(tmp_path: Path) -> None:
    database = tmp_path / "control/manifest.sqlite3"
    store = ManifestStore(database)
    record, kind = _register(store)

    assert kind is DuplicateKind.NEW_FILE
    assert record.status is ManifestStatus.DISCOVERED
    record = store.transition(record.file_id, ManifestStatus.VALIDATING)
    record = store.transition(
        record.file_id,
        ManifestStatus.VALIDATED,
        record_count=5,
        accepted_count=4,
        rejected_count=1,
    )
    record = store.transition(
        record.file_id,
        ManifestStatus.PROCESSING,
        processing_started_at=NOW.isoformat(),
    )
    record = store.transition(
        record.file_id,
        ManifestStatus.PROCESSED,
        processed_at=NOW.isoformat(),
        bronze_path="bronze/raw.csv",
    )

    reopened = ManifestStore(database).get(record.file_id)
    assert reopened is not None
    assert reopened.status is ManifestStatus.PROCESSED
    assert reopened.accepted_count == 4
    with pytest.raises(ManifestError, match="Invalid manifest transition"):
        store.transition(record.file_id, ManifestStatus.VALIDATING)


def test_manifest_distinguishes_name_and_checksum_relationships(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.sqlite3")
    original, _ = _register(store)

    same, same_kind = _register(store)
    assert same.file_id == original.file_id
    assert same_kind is DuplicateKind.SAME_NAME_SAME_CONTENT

    alias, alias_kind = _register(store, name="settlement_VCB_2026-07-22_009.csv")
    assert alias.file_id == original.file_id
    assert alias_kind is DuplicateKind.DIFFERENT_NAME_SAME_CONTENT

    changed, changed_kind = _register(store, checksum="b" * 64)
    assert changed.file_id != original.file_id
    assert changed_kind is DuplicateKind.SAME_NAME_CHANGED_CONTENT
    assert len(store.list_all()) == 2


def test_manifest_rejects_unknown_update_fields(tmp_path: Path) -> None:
    store = ManifestStore(tmp_path / "manifest.sqlite3")
    record, _ = _register(store)

    with pytest.raises(ManifestError, match="Unsupported"):
        store.transition(record.file_id, ManifestStatus.VALIDATING, unsafe_sql="value")
