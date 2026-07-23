from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from ingestion.batch.fixtures import FixtureConfig, generate_settlement_fixtures
from orchestration.tasks import ingest_settlements, process_silver, register_backfill_request

pytestmark = [pytest.mark.integration, pytest.mark.airflow_integration]


def test_settlement_and_silver_task_adapters_are_retry_safe(tmp_path: Path) -> None:
    inbound = tmp_path / "inbound"
    environment = {
        "PYTHONPATH": "src",
        "STORAGE_BACKEND": "local",
        "SETTLEMENT_BRONZE_DIR": str(tmp_path / "bronze"),
        "SETTLEMENT_QUARANTINE_DIR": str(tmp_path / "quarantine"),
        "SETTLEMENT_MANIFEST_DB": str(tmp_path / "control" / "settlement.sqlite3"),
        "SILVER_LOCAL_ROOT": str(tmp_path / "silver"),
        "SILVER_MANIFEST_DB": str(tmp_path / "control" / "silver.sqlite3"),
        "SILVER_TEMP_DIR": str(tmp_path / "tmp"),
        "SILVER_CODE_VERSION": "airflow-integration-v1",
        "SILVER_SCHEMA_VERSION": "silver-v1",
        "SILVER_SUPPORTED_CDC_SCHEMA": "cdc-bronze-v1",
        "SILVER_SETTLEMENT_CONTRACT": "contracts/batch/settlement_v1.yml",
        "SILVER_MAX_OBJECTS": "10",
        "AIRFLOW_TASK_TIMEOUT_SECONDS": "60",
    }
    fixtures = generate_settlement_fixtures(
        FixtureConfig(output_dir=inbound, partner_id="VCB", seed=707)
    )
    valid_file = fixtures["valid"]
    isolated = tmp_path / "isolated"
    isolated.mkdir()
    (isolated / valid_file.name).write_bytes(valid_file.read_bytes())

    ingested = ingest_settlements(
        input_dir=str(isolated),
        partner_id="VCB",
        contract="contracts/batch/settlement_v1.yml",
        storage_backend="local",
        environment=environment,
    )
    first = process_silver(
        source_type="SETTLEMENT",
        storage_backend="local",
        input_prefix="settlements/",
        environment=environment,
    )
    replay = process_silver(
        source_type="SETTLEMENT",
        storage_backend="local",
        input_prefix="settlements/",
        environment=environment,
    )

    assert ingested["status"] == "SUCCEEDED"
    assert first["records_written"] > 0
    assert replay["metadata"]["skipped_runs"] >= 1


def test_backfill_dry_run_does_not_require_or_write_control_state() -> None:
    result = register_backfill_request(
        params={
            "request_id": str(uuid4()),
            "source_type": "CDC",
            "entity": "customers",
            "dry_run": True,
        },
        airflow_run_id="manual__integration-dry-run",
        requested_by="integration-test",
        environment={},
    )

    assert result["dry_run"] is True
    assert result["control_written"] is False
