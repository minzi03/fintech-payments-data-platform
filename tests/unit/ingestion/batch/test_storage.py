"""Tests for local immutable Bronze and quarantine storage behavior."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from ingestion.batch.discovery import calculate_sha256
from ingestion.batch.models import RejectedRecord
from ingestion.batch.storage import ImmutableStorageError, LocalSettlementStorage


def test_bronze_copy_preserves_exact_bytes_and_partition_layout(tmp_path: Path) -> None:
    source = tmp_path / "inbound/settlement_VCB_2026-07-22_001.csv"
    source.parent.mkdir()
    source.write_bytes(b"header\r\nraw,bytes\r\n")
    checksum = calculate_sha256(source)
    storage = LocalSettlementStorage(tmp_path / "bronze", tmp_path / "quarantine")

    destination = storage.copy_to_bronze(
        source,
        partner_id="VCB",
        settlement_date=date(2026, 7, 22),
        ingestion_date=date(2026, 7, 23),
        checksum_sha256=checksum,
        metadata={"checksum_sha256": checksum, "ingestion_run_id": "run-1"},
    )

    assert destination.read_bytes() == source.read_bytes()
    assert "partner_id=VCB" in destination.as_posix()
    assert f"checksum={checksum}" in destination.as_posix()
    metadata = json.loads(
        destination.with_name(f"{destination.name}.metadata.json").read_text(encoding="utf-8")
    )
    assert metadata["checksum_sha256"] == checksum


def test_existing_bronze_path_with_different_bytes_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "settlement_VCB_2026-07-22_001.csv"
    source.write_bytes(b"original")
    checksum = calculate_sha256(source)
    storage = LocalSettlementStorage(tmp_path / "bronze", tmp_path / "quarantine")
    destination = storage.bronze_path(
        partner_id="VCB",
        settlement_date=date(2026, 7, 22),
        ingestion_date=date(2026, 7, 23),
        checksum_sha256=checksum,
        file_name=source.name,
    )
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"different")

    with pytest.raises(ImmutableStorageError, match="different content"):
        storage.copy_to_bronze(
            source,
            partner_id="VCB",
            settlement_date=date(2026, 7, 22),
            ingestion_date=date(2026, 7, 23),
            checksum_sha256=checksum,
            metadata={},
        )


def test_rejected_records_include_required_audit_fields(tmp_path: Path) -> None:
    storage = LocalSettlementStorage(tmp_path / "bronze", tmp_path / "quarantine")
    rejected = RejectedRecord(
        source_file="settlement_VCB_2026-07-22_002.csv",
        source_row_number=3,
        raw_record={"amount": "bad"},
        error_code="INVALID_DECIMAL",
        error_message="amount is invalid",
        rejected_at=datetime(2026, 7, 23, tzinfo=UTC),
        ingestion_run_id="run-2",
    )

    destination = storage.write_rejected_records(
        [rejected],
        source_file_name=rejected.source_file,
        partner_id="VCB",
        settlement_date=date(2026, 7, 22),
        ingestion_date=date(2026, 7, 23),
        checksum_sha256="c" * 64,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["source_row_number"] == 3
    assert payload["raw_record"] == {"amount": "bad"}
    assert payload["ingestion_run_id"] == "run-2"
