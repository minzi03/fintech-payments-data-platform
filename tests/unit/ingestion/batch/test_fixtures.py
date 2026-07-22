"""Tests for deterministic settlement scenario fixture generation."""

from datetime import date
from pathlib import Path

from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures


def test_fixture_generation_is_deterministic_and_complete(tmp_path: Path) -> None:
    first = generate_settlement_fixtures(
        FixtureConfig(tmp_path / "first", "VCB", date(2026, 7, 22), 91)
    )
    second = generate_settlement_fixtures(
        FixtureConfig(tmp_path / "second", "VCB", date(2026, 7, 22), 91)
    )

    assert set(first) == {
        "valid",
        "duplicate_rows",
        "invalid_amount",
        "invalid_currency",
        "invalid_status",
        "invalid_schema",
        "empty_file",
        "changed_content",
    }
    assert all(first[name].read_bytes() == second[name].read_bytes() for name in first)
    assert first["changed_content"].name == first["valid"].name
    valid_text = first["valid"].read_text(encoding="utf-8")
    for scenario in (
        "MATCHED",
        "MISSING_INTERNAL",
        "AMOUNT_MISMATCH",
        "CURRENCY_MISMATCH",
        "STATUS_MISMATCH",
    ):
        assert scenario in valid_text
