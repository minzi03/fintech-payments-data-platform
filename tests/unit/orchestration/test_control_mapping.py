from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from orchestration.config import ControlDatabaseSettings
from orchestration.control import ControlStore
from orchestration.models import (
    BackfillRequest,
    PipelineRunType,
    PipelineStatus,
    QualityClassification,
    QualityResult,
    TaskResult,
    deterministic_pipeline_run_id,
    safe_task_run_id,
)


def test_pipeline_and_task_identity_are_deterministic() -> None:
    first = deterministic_pipeline_run_id("dag", "scheduled__2026-07-23")
    second = deterministic_pipeline_run_id("dag", "scheduled__2026-07-23")

    assert first == second
    assert safe_task_run_id(first, "task", 1) == safe_task_run_id(first, "task", 1)
    assert safe_task_run_id(first, "task", 1) != safe_task_run_id(first, "task", 2)


def test_task_result_xcom_contains_metadata_not_record_payload() -> None:
    result = TaskResult(
        status=PipelineStatus.SUCCEEDED,
        records_read=10,
        records_written=9,
        records_rejected=1,
        input_assets=("s3://bronze/object",),
        output_assets=("s3://silver/object",),
        metadata={"run_id": str(uuid4()), "logical_date": datetime.now(UTC).isoformat()},
    ).to_xcom()

    assert result["status"] == "SUCCEEDED"
    assert "payload" not in result
    assert PipelineRunType.BACKFILL.value == "BACKFILL"


def test_control_store_maps_pipeline_task_quality_and_backfill(monkeypatch) -> None:
    store = ControlStore(
        ControlDatabaseSettings.from_env(
            {"AIRFLOW_CONN_CONTROL_DB": "postgresql://control:redacted@db/airflow"}
        )
    )
    calls: list[tuple[str, tuple[object, ...]]] = []

    def fake_one(query: str, parameters: tuple[object, ...]):
        calls.append((query, parameters))
        if "task_runs" in query:
            return {"task_run_id": parameters[0], "status": parameters[4]}
        if "count(*)" in query:
            return {"count": 1}
        return {"pipeline_run_id": parameters[-1], "status": parameters[0]}

    monkeypatch.setattr(store, "_one", fake_one)
    pipeline_run_id = uuid4()
    store.register_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        dag_id="dag",
        airflow_run_id="manual__run",
        pipeline_name="pipeline",
        logical_date=datetime.now(UTC),
        run_type=PipelineRunType.MANUAL,
    )
    store.mark_pipeline_running(pipeline_run_id)
    store.record_task_run(
        pipeline_run_id=pipeline_run_id,
        task_id="task",
        try_number=1,
        status=PipelineStatus.SUCCEEDED,
        result_metadata={"count": 1},
    )
    store.record_quality_results(
        pipeline_run_id,
        [
            QualityResult(
                rule_name="freshness",
                classification=QualityClassification.PASS,
                observed_value=1,
                warn_threshold=10,
                fail_threshold=20,
                details={},
            )
        ],
    )
    store.register_backfill(
        BackfillRequest(
            request_id=uuid4(),
            source_type="CDC",
            entity_name="customers",
            input_prefix="cdc/",
            from_date=None,
            to_date=None,
            force_reprocess=False,
            dry_run=False,
        ),
        pipeline_name="backfill",
        requested_by="unit-test",
        airflow_run_id="manual__backfill",
    )
    store.complete_pipeline(
        pipeline_run_id,
        status=PipelineStatus.SUCCEEDED,
        records_read=1,
        records_written=1,
        records_rejected=0,
    )

    assert len(calls) == 6
    assert all("redacted" not in query for query, _ in calls)
    with pytest.raises(ValueError, match="terminal"):
        store.complete_pipeline(
            pipeline_run_id,
            status=PipelineStatus.RUNNING,
            records_read=0,
            records_written=0,
            records_rejected=0,
        )
