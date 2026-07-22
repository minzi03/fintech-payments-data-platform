"""Tests for settlement discovery, naming, checksum, and stable identity."""

import hashlib
from datetime import date
from pathlib import Path

import pytest

from ingestion.batch.contracts import load_settlement_contract
from ingestion.batch.discovery import (
    DiscoveryError,
    calculate_sha256,
    deterministic_file_id,
    discover_files,
    parse_settlement_filename,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = load_settlement_contract(REPOSITORY_ROOT / "contracts/batch/settlement_v1.yml")


def test_parse_valid_settlement_file_name() -> None:
    metadata = parse_settlement_filename(
        Path("settlement_VCB_2026-07-22_001.csv"), CONTRACT.naming_pattern, "VCB"
    )

    assert metadata.partner_id == "VCB"
    assert metadata.settlement_date == date(2026, 7, 22)
    assert metadata.sequence == 1


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("settlement_VCB_2026-07-22_001.txt", "INVALID_EXTENSION"),
        ("VCB_2026-07-22_001.csv", "INVALID_FILE_NAME"),
        ("settlement_VCB_2026-02-30_001.csv", "INVALID_SETTLEMENT_DATE"),
        ("settlement_VCB_2026-07-22_000.csv", "INVALID_FILE_SEQUENCE"),
    ],
)
def test_invalid_file_names_are_rejected(name: str, code: str) -> None:
    with pytest.raises(DiscoveryError) as raised:
        parse_settlement_filename(Path(name), CONTRACT.naming_pattern, "VCB")
    assert raised.value.code == code


def test_partner_in_file_name_must_match_expected_partner() -> None:
    with pytest.raises(DiscoveryError) as raised:
        parse_settlement_filename(
            Path("settlement_TCB_2026-07-22_001.csv"), CONTRACT.naming_pattern, "VCB"
        )
    assert raised.value.code == "PARTNER_FILE_NAME_MISMATCH"


def test_checksum_and_file_id_are_deterministic(tmp_path: Path) -> None:
    source = tmp_path / "sample.csv"
    source.write_bytes(b"exact raw bytes\n")

    checksum = calculate_sha256(source, chunk_size=3)
    assert checksum == hashlib.sha256(b"exact raw bytes\n").hexdigest()
    assert deterministic_file_id("settlement", "VCB", checksum) == deterministic_file_id(
        "settlement", "VCB", checksum
    )
    assert deterministic_file_id("settlement", "VCB", checksum) != deterministic_file_id(
        "settlement", "TCB", checksum
    )


def test_discovery_modes_are_mutually_exclusive_and_sorted(tmp_path: Path) -> None:
    first = tmp_path / "b.csv"
    second = tmp_path / "a.csv"
    first.touch()
    second.touch()

    assert discover_files(file=None, input_dir=tmp_path) == (second, first)
    assert discover_files(file=first, input_dir=None) == (first,)
    with pytest.raises(DiscoveryError, match="exactly one"):
        discover_files(file=first, input_dir=tmp_path)
