"""Validated, concurrency-limited manual Silver backfill workflow."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import Param, dag, get_current_context, task

from orchestration.callbacks import failure_callback
from orchestration.config import OrchestrationSettings, validate_backfill_params
from orchestration.tasks import process_silver, register_backfill_request

SETTINGS = OrchestrationSettings.from_env(os.environ)


@dag(
    dag_id="data_platform_backfill",
    description="Manual and validated CDC or settlement Silver backfill.",
    schedule=None,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=1,
    tags=["fintech", "backfill", "recovery", "silver", "phase-7"],
    params={
        "request_id": Param("", type="string", description="Required UUID request ID"),
        "source_type": Param("CDC", enum=["CDC", "SETTLEMENT"]),
        "entity": Param(None, type=["null", "string"]),
        "input_prefix": Param(None, type=["null", "string"]),
        "from_date": Param(None, type=["null", "string"]),
        "to_date": Param(None, type=["null", "string"]),
        "force_reprocess": Param(False, type="boolean"),
        "dry_run": Param(True, type="boolean"),
    },
    default_args={
        "owner": "data-platform",
        "retries": 1,
        "retry_delay": timedelta(seconds=SETTINGS.retry_delay_seconds),
        "execution_timeout": timedelta(seconds=SETTINGS.execution_timeout_seconds),
        "on_failure_callback": failure_callback,
    },
    doc_md="""
    Manual-only workflow. Parameters are allowlisted and never converted into a shell
    command. Dry-run validates and reads inputs without writing Silver or control state.
    """,
)
def data_platform_backfill():
    @task(task_id="validate_backfill_request")
    def validate_request() -> dict[str, object]:
        context = get_current_context()
        request = validate_backfill_params(context["params"])
        return {
            "request_id": str(request.request_id),
            "source_type": request.source_type,
            "entity": request.entity_name,
            "input_prefix": request.input_prefix,
            "from_date": request.from_date.isoformat() if request.from_date else None,
            "to_date": request.to_date.isoformat() if request.to_date else None,
            "force_reprocess": request.force_reprocess,
            "dry_run": request.dry_run,
        }

    @task(task_id="register_backfill_request")
    def register_request(request: dict[str, object]) -> dict[str, object]:
        context = get_current_context()
        return register_backfill_request(
            params=request,
            airflow_run_id=context["run_id"],
            requested_by="airflow-local-operator",
        )

    @task(task_id="execute_backfill")
    def execute(request: dict[str, object]) -> dict[str, object]:
        from_date = request.get("from_date")
        to_date = request.get("to_date")
        return process_silver(
            source_type=str(request["source_type"]),
            storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
            input_prefix=str(request["input_prefix"]) if request.get("input_prefix") else None,
            entity=str(request["entity"]) if request.get("entity") else None,
            from_date=datetime.fromisoformat(str(from_date)).date() if from_date else None,
            to_date=datetime.fromisoformat(str(to_date)).date() if to_date else None,
            force_reprocess=bool(request["force_reprocess"]),
            dry_run=bool(request["dry_run"]),
            max_objects=int(os.environ.get("SILVER_MAX_OBJECTS", "100")),
        )

    validated = validate_request()
    registered = register_request(validated)
    execute(registered)


data_platform_backfill()
