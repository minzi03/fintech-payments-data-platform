"""Tests for Phase 2 CLI argument and fixture behavior."""

import json
from pathlib import Path

import pytest

from ingestion.batch.cli import build_parser, main


def test_ingest_cli_requires_exactly_one_input_mode() -> None:
    parser = build_parser({})

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "ingest-settlements",
                "--partner-id",
                "VCB",
                "--contract",
                "contract.yml",
            ]
        )


def test_ingest_cli_accepts_environment_or_explicit_storage_backend() -> None:
    parser = build_parser({"STORAGE_BACKEND": "minio"})
    environment_default = parser.parse_args(
        [
            "ingest-settlements",
            "--file",
            "file.csv",
            "--partner-id",
            "VCB",
            "--contract",
            "contract.yml",
        ]
    )
    explicit_local = parser.parse_args(
        [
            "ingest-settlements",
            "--file",
            "file.csv",
            "--partner-id",
            "VCB",
            "--contract",
            "contract.yml",
            "--storage-backend",
            "local",
        ]
    )

    assert environment_default.storage_backend == "minio"
    assert explicit_local.storage_backend == "local"
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "ingest-settlements",
                "--file",
                "file.csv",
                "--input-dir",
                "input",
                "--partner-id",
                "VCB",
                "--contract",
                "contract.yml",
            ]
        )


def test_fixture_cli_generates_structured_result(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    result = main(
        [
            "generate-settlement-fixtures",
            "--output-dir",
            str(tmp_path),
            "--partner-id",
            "VCB",
            "--settlement-date",
            "2026-07-22",
            "--seed",
            "73",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert result == 0
    assert set(payload["generated"]) >= {"valid", "invalid_schema", "changed_content"}
    assert all(Path(path).is_file() for path in payload["generated"].values())


def test_cli_rejects_invalid_partner_id(tmp_path: Path) -> None:
    assert (
        main(
            [
                "generate-settlement-fixtures",
                "--output-dir",
                str(tmp_path),
                "--partner-id",
                "bad-partner",
            ]
        )
        == 2
    )
