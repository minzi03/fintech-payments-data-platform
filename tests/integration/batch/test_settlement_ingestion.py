"""End-to-end local settlement manifest, Bronze, and quarantine integration tests."""

from __future__ import annotations

import itertools
import shutil
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ingestion.batch.contracts import load_settlement_contract
from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures
from ingestion.batch.manifest import ManifestStore
from ingestion.batch.models import DuplicateKind, ManifestStatus
from ingestion.batch.settlement_ingestor import SettlementIngestor
from ingestion.batch.storage import LocalSettlementStorage

pytestmark = [pytest.mark.integration, pytest.mark.batch_integration]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts/batch/settlement_v1.yml"
FIXED_NOW = datetime(2026, 7, 23, 6, 30, tzinfo=UTC)


def _environment(tmp_path: Path):
    inbound = tmp_path / "inbound"
    fixtures = generate_settlement_fixtures(FixtureConfig(inbound, "VCB", date(2026, 7, 22), 42))
    manifest = ManifestStore(tmp_path / "control/settlement_manifest.sqlite3")
    storage = LocalSettlementStorage(tmp_path / "bronze", tmp_path / "quarantine")
    run_counter = itertools.count(1)
    ingestor = SettlementIngestor(
        contract=load_settlement_contract(CONTRACT_PATH),
        manifest=manifest,
        storage=storage,
        clock=lambda: FIXED_NOW,
        run_id_factory=lambda: f"integration-run-{next(run_counter)}",
    )
    return fixtures, manifest, storage, ingestor


def test_ingest_valid_csv_preserves_immutable_raw_and_manifest(tmp_path: Path) -> None:
    fixtures, manifest, _storage, ingestor = _environment(tmp_path)
    source_bytes = fixtures["valid"].read_bytes()

    result = ingestor.ingest_file(fixtures["valid"], expected_partner_id="VCB")

    assert result.status is ManifestStatus.PROCESSED
    assert result.record_count == 5
    assert result.accepted_count == 5
    assert result.rejected_count == 0
    assert result.bronze_path is not None
    bronze_path = Path(result.bronze_path)
    assert bronze_path.read_bytes() == source_bytes
    assert bronze_path.with_name(f"{bronze_path.name}.metadata.json").is_file()

    persisted = ManifestStore(manifest.database_path).get(result.file_id or "")
    assert persisted is not None
    assert persisted.checksum_sha256 == result.checksum_sha256
    assert persisted.status is ManifestStatus.PROCESSED


def test_partially_invalid_csv_writes_rejections_and_still_processes(tmp_path: Path) -> None:
    fixtures, manifest, _storage, ingestor = _environment(tmp_path)

    result = ingestor.ingest_file(fixtures["duplicate_rows"], expected_partner_id="VCB")

    assert result.status is ManifestStatus.PROCESSED
    assert result.accepted_count == 1
    assert result.rejected_count == 1
    assert result.bronze_path is not None and Path(result.bronze_path).is_file()
    assert result.quarantine_path is not None and Path(result.quarantine_path).is_file()
    rejected_payload = Path(result.quarantine_path).read_text(encoding="utf-8")
    assert "DUPLICATE_ROW" in rejected_payload
    persisted = manifest.get(result.file_id or "")
    assert persisted is not None
    assert persisted.status is ManifestStatus.PROCESSED


def test_invalid_file_schema_is_quarantined_without_bronze(tmp_path: Path) -> None:
    fixtures, _manifest, _storage, ingestor = _environment(tmp_path)
    source_bytes = fixtures["invalid_schema"].read_bytes()

    result = ingestor.ingest_file(fixtures["invalid_schema"], expected_partner_id="VCB")

    assert result.status is ManifestStatus.QUARANTINED
    assert result.error_code == "INVALID_FILE_SCHEMA"
    assert result.bronze_path is None
    assert result.quarantine_path is not None
    assert Path(result.quarantine_path).read_bytes() == source_bytes


def test_rerun_same_name_and_checksum_is_skipped(tmp_path: Path) -> None:
    fixtures, manifest, _storage, ingestor = _environment(tmp_path)

    first = ingestor.ingest_file(fixtures["valid"], expected_partner_id="VCB")
    second = ingestor.ingest_file(fixtures["valid"], expected_partner_id="VCB")

    assert first.status is ManifestStatus.PROCESSED
    assert second.status is ManifestStatus.PROCESSED
    assert second.skipped
    assert second.duplicate_kind is DuplicateKind.SAME_NAME_SAME_CONTENT
    assert len(manifest.list_all()) == 1


def test_same_filename_changed_content_creates_new_manifest_version(tmp_path: Path) -> None:
    fixtures, manifest, _storage, ingestor = _environment(tmp_path)

    first = ingestor.ingest_file(fixtures["valid"], expected_partner_id="VCB")
    changed = ingestor.ingest_file(fixtures["changed_content"], expected_partner_id="VCB")

    assert changed.status is ManifestStatus.PROCESSED
    assert changed.duplicate_kind is DuplicateKind.SAME_NAME_CHANGED_CONTENT
    assert changed.file_id != first.file_id
    assert changed.checksum_sha256 != first.checksum_sha256
    assert len(manifest.list_all()) == 2


def test_different_filename_same_content_is_checksum_duplicate(tmp_path: Path) -> None:
    fixtures, manifest, _storage, ingestor = _environment(tmp_path)
    alias = fixtures["valid"].with_name("settlement_VCB_2026-07-22_009.csv")
    shutil.copyfile(fixtures["valid"], alias)

    first = ingestor.ingest_file(fixtures["valid"], expected_partner_id="VCB")
    duplicate = ingestor.ingest_file(alias, expected_partner_id="VCB")

    assert duplicate.file_id == first.file_id
    assert duplicate.skipped
    assert duplicate.duplicate_kind is DuplicateKind.DIFFERENT_NAME_SAME_CONTENT
    assert len(manifest.list_all()) == 1


def test_bronze_copy_does_not_change_when_inbound_changes_after_processing(
    tmp_path: Path,
) -> None:
    fixtures, _manifest, _storage, ingestor = _environment(tmp_path)
    result = ingestor.ingest_file(fixtures["valid"], expected_partner_id="VCB")
    assert result.bronze_path is not None
    bronze_path = Path(result.bronze_path)
    bronze_bytes = bronze_path.read_bytes()

    fixtures["valid"].write_bytes(b"changed after ingestion")

    assert bronze_path.read_bytes() == bronze_bytes


def test_failed_bronze_write_never_marks_manifest_processed(tmp_path: Path) -> None:
    fixtures, manifest, storage, _ingestor = _environment(tmp_path)

    class FailingBronzeStorage(LocalSettlementStorage):
        def copy_to_bronze(self, *args, **kwargs):
            raise OSError("simulated Bronze outage")

    ingestor = SettlementIngestor(
        contract=load_settlement_contract(CONTRACT_PATH),
        manifest=manifest,
        storage=FailingBronzeStorage(storage.bronze_root, storage.quarantine_root),
        clock=lambda: FIXED_NOW,
        run_id_factory=lambda: "failed-bronze-run",
    )

    result = ingestor.ingest_file(fixtures["valid"], expected_partner_id="VCB")

    assert result.status is ManifestStatus.FAILED
    assert result.bronze_path is None
    persisted = manifest.get(result.file_id or "")
    assert persisted is not None
    assert persisted.status is ManifestStatus.FAILED
    assert all(record.status is not ManifestStatus.PROCESSED for record in manifest.list_all())
