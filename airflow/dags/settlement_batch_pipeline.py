"""Daily settlement ingestion and Silver orchestration."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, get_current_context, task

from orchestration.callbacks import failure_callback
from orchestration.config import OrchestrationSettings
from orchestration.tasks import (
    begin_pipeline,
    discover_settlement_inputs,
    discover_unprocessed_bronze,
    finish_pipeline,
    ingest_settlements,
    process_silver,
    record_task_result,
    validate_ingestion_result,
)

SETTINGS = OrchestrationSettings.from_env(os.environ)


@dag(
    dag_id="settlement_batch_pipeline",
    description="Ingest banking-partner settlement files and publish normalized Silver records.",
    schedule=SETTINGS.settlement_schedule,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["fintech", "settlement", "batch", "silver", "phase-7"],
    default_args={
        "owner": "data-platform",
        "retries": SETTINGS.task_retries,
        "retry_delay": timedelta(seconds=SETTINGS.retry_delay_seconds),
        "execution_timeout": timedelta(seconds=SETTINGS.execution_timeout_seconds),
        "on_failure_callback": failure_callback,
    },
    doc_md="""
    Orchestrates existing settlement ingestion and Silver services. XCom contains only
    paths/URIs, IDs, counts, and classifications. Reconciliation is not part of this DAG.
    """,
)
def settlement_batch_pipeline():
    @task(task_id="start")
    def start() -> str:
        context = get_current_context()
        return begin_pipeline(
            dag_id=context["dag"].dag_id,
            airflow_run_id=context["run_id"],
            pipeline_name="settlement-batch-to-silver",
            logical_date=context["logical_date"],
            run_type=_run_type(context["dag_run"].run_type),
        )

    @task
    def discover_inbound_files(pipeline_run_id: str) -> dict[str, object]:
        result = discover_settlement_inputs(
            os.environ.get(
                "SETTLEMENT_INBOUND_DIR",
                "/opt/airflow/project/data/inbound/settlements",
            )
        )
        _record(pipeline_run_id, "discover_inbound_files", result)
        return result

    @task
    def ingest_settlement_files(
        pipeline_run_id: str, discovery: dict[str, object]
    ) -> dict[str, object]:
        if not discovery.get("input_assets"):
            result = {"status": "SKIPPED", "metadata": {"file_count": 0}}
        else:
            result = ingest_settlements(
                input_dir=os.environ.get(
                    "SETTLEMENT_INBOUND_DIR",
                    "/opt/airflow/project/data/inbound/settlements",
                ),
                partner_id=os.environ.get("SETTLEMENT_PARTNER_ID", "VCB"),
                contract=os.environ.get(
                    "SILVER_SETTLEMENT_CONTRACT",
                    "/opt/airflow/project/contracts/batch/settlement_v1.yml",
                ),
                storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
            )
        _record(pipeline_run_id, "ingest_settlement_files", result)
        return result

    @task
    def validate_ingestion_results(
        pipeline_run_id: str, result: dict[str, object]
    ) -> dict[str, object]:
        validated = validate_ingestion_result(result)
        _record(pipeline_run_id, "validate_ingestion_results", validated)
        return validated

    @task
    def discover_unprocessed_settlement_bronze(
        pipeline_run_id: str, _: dict[str, object]
    ) -> dict[str, object]:
        result = discover_unprocessed_bronze(
            source_type="SETTLEMENT",
            storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
            input_prefix="settlements/",
        )
        _record(pipeline_run_id, "discover_unprocessed_settlement_bronze", result)
        return result

    @task
    def process_settlement_silver(
        pipeline_run_id: str, discovery: dict[str, object]
    ) -> dict[str, object]:
        if not discovery.get("input_assets"):
            result = {"status": "SKIPPED", "metadata": {"discovered_objects": 0}}
        else:
            result = process_silver(
                source_type="SETTLEMENT",
                storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
                input_prefix="settlements/",
            )
        _record(pipeline_run_id, "process_settlement_silver", result)
        return result

    @task(task_id="run_settlement_quality_gate")
    def quality_gate(
        pipeline_run_id: str,
        ingestion: dict[str, object],
        silver: dict[str, object],
    ) -> dict[str, object]:
        return finish_pipeline(
            pipeline_run_id=pipeline_run_id,
            results=[ingestion, silver],
            warn_rate=SETTINGS.settlement_warn_rate,
            fail_rate=SETTINGS.settlement_fail_rate,
        )

    @task(task_id="record_pipeline_success")
    def record_success(result: dict[str, object]) -> dict[str, object]:
        return {"pipeline_run_id": result["pipeline_run_id"], "status": result["status"]}

    pipeline_run_id = start()
    discovered = discover_inbound_files(pipeline_run_id)
    ingested = ingest_settlement_files(pipeline_run_id, discovered)
    validated = validate_ingestion_results(pipeline_run_id, ingested)
    bronze = discover_unprocessed_settlement_bronze(pipeline_run_id, validated)
    silver = process_settlement_silver(pipeline_run_id, bronze)
    gated = quality_gate(pipeline_run_id, ingested, silver)
    record_success(gated)


def _record(pipeline_run_id: str, task_id: str, result: dict[str, object]) -> None:
    context = get_current_context()
    record_task_result(
        pipeline_run_id=pipeline_run_id,
        task_id=task_id,
        result=result,
        try_number=int(context["task_instance"].try_number),
    )


def _run_type(value: object) -> str:
    normalized = str(getattr(value, "value", value)).upper()
    return normalized if normalized in {"SCHEDULED", "MANUAL", "BACKFILL"} else "RECOVERY"


settlement_batch_pipeline()
