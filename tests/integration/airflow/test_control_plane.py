from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from orchestration.callbacks import failure_callback
from orchestration.models import (
    PipelineRunType,
    PipelineStatus,
    deterministic_pipeline_run_id,
)
from orchestration.quality import rejection_rate_result

pytestmark = [pytest.mark.integration, pytest.mark.airflow_integration]


def test_pipeline_task_quality_and_failure_state_are_persistent(control_store) -> None:
    suffix = uuid4().hex
    pipeline_run_id = uuid4()
    control_store.register_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        dag_id=f"integration-{suffix}",
        airflow_run_id=f"manual__{suffix}",
        pipeline_name="integration-control-plane",
        logical_date=datetime.now(UTC),
        run_type=PipelineRunType.MANUAL,
    )
    control_store.mark_pipeline_running(pipeline_run_id)
    control_store.record_task_run(
        pipeline_run_id=pipeline_run_id,
        task_id="fixture_task",
        try_number=1,
        status=PipelineStatus.SUCCEEDED,
        records_read=10,
        records_written=9,
        records_rejected=1,
        result_metadata={"fixture": True},
    )
    quality = rejection_rate_result(
        records_read=10, records_rejected=1, warn_rate=0.05, fail_rate=0.2
    )
    control_store.record_quality_results(pipeline_run_id, [quality])
    completed = control_store.complete_pipeline(
        pipeline_run_id,
        status=PipelineStatus.PARTIAL,
        records_read=10,
        records_written=9,
        records_rejected=1,
    )

    assert completed["status"] == "PARTIAL"
    counts = control_store.table_counts()
    assert counts["pipeline_runs"] >= 1
    assert counts["task_runs"] >= 1
    assert counts["data_quality_results"] >= 1


def test_retry_is_idempotent_for_same_task_try(control_store) -> None:
    suffix = uuid4().hex
    pipeline_run_id = uuid4()
    control_store.register_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        dag_id=f"retry-{suffix}",
        airflow_run_id=f"manual__{suffix}",
        pipeline_name="retry-control-plane",
        logical_date=datetime.now(UTC),
        run_type=PipelineRunType.RECOVERY,
    )

    first = control_store.record_task_run(
        pipeline_run_id=pipeline_run_id,
        task_id="idempotent_task",
        try_number=1,
        status=PipelineStatus.FAILED,
    )
    retried = control_store.record_task_run(
        pipeline_run_id=pipeline_run_id,
        task_id="idempotent_task",
        try_number=1,
        status=PipelineStatus.SUCCEEDED,
    )

    assert first["task_run_id"] == retried["task_run_id"]
    assert retried["status"] == "SUCCEEDED"


def test_failure_callback_records_failed_without_sensitive_context(control_store) -> None:
    suffix = uuid4().hex
    dag_id = f"failure-{suffix}"
    airflow_run_id = f"manual__{suffix}"
    pipeline_run_id = deterministic_pipeline_run_id(dag_id, airflow_run_id)
    control_store.register_pipeline_run(
        pipeline_run_id=pipeline_run_id,
        dag_id=dag_id,
        airflow_run_id=airflow_run_id,
        pipeline_name="failure-callback-test",
        logical_date=datetime.now(UTC),
        run_type=PipelineRunType.MANUAL,
    )
    control_store.mark_pipeline_running(pipeline_run_id)

    failure_callback(
        {
            "task_instance": SimpleNamespace(dag_id=dag_id, task_id="failing_task", try_number=1),
            "dag_run": SimpleNamespace(run_id=airflow_run_id),
            "logical_date": datetime.now(UTC),
            "exception": RuntimeError("password=must-not-be-persisted"),
        }
    )

    failed = control_store.get_pipeline_run(pipeline_run_id)
    assert failed is not None
    assert failed["status"] == "FAILED"
    assert "must-not-be-persisted" not in str(failed)
