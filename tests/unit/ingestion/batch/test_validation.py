"""Tests for settlement file and record-level validation rules."""

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from ingestion.batch.contracts import load_settlement_contract
from ingestion.batch.discovery import parse_settlement_filename
from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures
from ingestion.batch.validation import validate_settlement_file

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CONTRACT = load_settlement_contract(REPOSITORY_ROOT / "contracts/batch/settlement_v1.yml")
NOW = datetime(2026, 7, 23, tzinfo=UTC)


def _validate(path: Path):
    filename = parse_settlement_filename(path, CONTRACT.naming_pattern, "VCB")
    return validate_settlement_file(
        path,
        CONTRACT,
        filename,
        rejected_at=NOW,
        ingestion_run_id="unit-run",
    )


def test_valid_candidates_keep_decimal_precision_and_utc_timestamps(tmp_path: Path) -> None:
    paths = generate_settlement_fixtures(FixtureConfig(tmp_path, "VCB", date(2026, 7, 22), 42))
    result = _validate(paths["valid"])

    assert result.file_is_valid
    assert result.record_count == 5
    assert len(result.accepted_records) == 5
    assert not result.rejected_records
    assert all(isinstance(record.amount, Decimal) for record in result.accepted_records)
    assert all(record.amount.as_tuple().exponent == -2 for record in result.accepted_records)
    assert all(record.transaction_timestamp.tzinfo is UTC for record in result.accepted_records)
    assert any(record.internal_transaction_id is None for record in result.accepted_records)


def test_duplicate_row_rejects_only_the_second_record(tmp_path: Path) -> None:
    paths = generate_settlement_fixtures(FixtureConfig(tmp_path))
    result = _validate(paths["duplicate_rows"])

    assert result.record_count == 2
    assert len(result.accepted_records) == 1
    assert len(result.rejected_records) == 1
    assert result.rejected_records[0].error_code == "DUPLICATE_ROW"


def test_invalid_amount_currency_status_and_net_are_rejected(tmp_path: Path) -> None:
    paths = generate_settlement_fixtures(FixtureConfig(tmp_path))

    assert (
        "AMOUNT_NOT_POSITIVE" in _validate(paths["invalid_amount"]).rejected_records[0].error_code
    )
    assert "INVALID_CURRENCY" in _validate(paths["invalid_currency"]).rejected_records[0].error_code
    assert (
        "INVALID_SETTLEMENT_STATUS"
        in _validate(paths["invalid_status"]).rejected_records[0].error_code
    )

    net_file = paths["valid"]
    net_file.write_text(
        net_file.read_text(encoding="utf-8").replace("98.75", "99.99", 1),
        encoding="utf-8",
    )
    assert "NET_AMOUNT_INCONSISTENT" in _validate(net_file).rejected_records[0].error_code


def test_invalid_schema_and_empty_file_are_file_level_rejections(tmp_path: Path) -> None:
    paths = generate_settlement_fixtures(FixtureConfig(tmp_path))

    invalid_schema = _validate(paths["invalid_schema"])
    empty = _validate(paths["empty_file"])

    assert invalid_schema.file_issues[0].code == "INVALID_FILE_SCHEMA"
    assert not invalid_schema.rejected_records
    assert empty.file_issues[0].code == "EMPTY_FILE"


def test_required_value_and_timezone_rules_reject_only_affected_rows(tmp_path: Path) -> None:
    paths = generate_settlement_fixtures(FixtureConfig(tmp_path))
    valid_file = paths["valid"]
    content = valid_file.read_text(encoding="utf-8")
    content = content.replace("MATCHED-42-0001", "", 1)
    content = content.replace("2026-07-22T12:02:00Z", "2026-07-22T12:02:00", 1)
    valid_file.write_text(content, encoding="utf-8")

    rejected = _validate(valid_file).rejected_records

    assert len(rejected) == 2
    assert "REQUIRED_FIELD_EMPTY" in rejected[0].error_code
    assert "TIMESTAMP_TIMEZONE_REQUIRED" in rejected[1].error_code


def test_decimal_syntax_and_scale_are_validated_without_float_conversion(tmp_path: Path) -> None:
    first = generate_settlement_fixtures(FixtureConfig(tmp_path / "syntax"))["invalid_amount"]
    first.write_text(
        first.read_text(encoding="utf-8").replace("-5.00", "not-a-decimal"),
        encoding="utf-8",
    )
    assert "INVALID_DECIMAL" in _validate(first).rejected_records[0].error_code

    second = generate_settlement_fixtures(FixtureConfig(tmp_path / "scale"))["valid"]
    second.write_text(
        second.read_text(encoding="utf-8").replace("100.00", "100.001", 1),
        encoding="utf-8",
    )
    assert "DECIMAL_SCALE_EXCEEDED" in _validate(second).rejected_records[0].error_code


def test_duplicate_business_key_is_detected_even_when_rows_differ(tmp_path: Path) -> None:
    valid_file = generate_settlement_fixtures(FixtureConfig(tmp_path))["valid"]
    valid_file.write_text(
        valid_file.read_text(encoding="utf-8").replace(
            "MISSING_INTERNAL-42-0002", "MATCHED-42-0001", 1
        ),
        encoding="utf-8",
    )

    rejected = _validate(valid_file).rejected_records

    assert len(rejected) == 1
    assert rejected[0].error_code == "DUPLICATE_SETTLEMENT_REFERENCE"
