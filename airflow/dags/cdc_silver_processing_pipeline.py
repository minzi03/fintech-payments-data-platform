"""Dependency-aware CDC Bronze-to-Silver orchestration."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

from airflow.sdk import dag, get_current_context, task

from orchestration.callbacks import failure_callback
from orchestration.config import OrchestrationSettings
from orchestration.tasks import (
    begin_pipeline,
    discover_unprocessed_bronze,
    finish_pipeline,
    process_silver,
    record_task_result,
)

SETTINGS = OrchestrationSettings.from_env(os.environ)
ENTITIES = (
    "customers",
    "accounts",
    "merchants",
    "payment_transactions",
    "transaction_events",
    "refunds",
)


@dag(
    dag_id="cdc_silver_processing_pipeline",
    description="Incrementally publish CDC history, latest-all, current, and quality outputs.",
    schedule=SETTINGS.silver_schedule,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=2,
    tags=["fintech", "cdc", "silver", "quality", "phase-7"],
    default_args={
        "owner": "data-platform",
        "retries": SETTINGS.task_retries,
        "retry_delay": timedelta(seconds=SETTINGS.retry_delay_seconds),
        "execution_timeout": timedelta(seconds=SETTINGS.execution_timeout_seconds),
        "on_failure_callback": failure_callback,
    },
    doc_md="""
    Calls the existing Phase 6 processor by entity. Only object references and aggregate
    counts use XCom. Entity dependencies preserve the current reference-quality policy.
    """,
)
def cdc_silver_processing_pipeline():
    @task(task_id="start")
    def start() -> str:
        context = get_current_context()
        run_type = str(getattr(context["dag_run"].run_type, "value", "MANUAL")).upper()
        if run_type not in {"SCHEDULED", "MANUAL", "BACKFILL"}:
            run_type = "RECOVERY"
        return begin_pipeline(
            dag_id=context["dag"].dag_id,
            airflow_run_id=context["run_id"],
            pipeline_name="cdc-bronze-to-silver",
            logical_date=context["logical_date"],
            run_type=run_type,
        )

    @task
    def discover_unprocessed_cdc_bronze(pipeline_run_id: str) -> dict[str, object]:
        result = discover_unprocessed_bronze(
            source_type="CDC",
            storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
            input_prefix="cdc/",
            max_objects=int(os.environ.get("SILVER_MAX_OBJECTS", "100")),
        )
        _record(pipeline_run_id, "discover_unprocessed_cdc_bronze", result)
        return result

    @task
    def process_entity(
        pipeline_run_id: str, discovery: dict[str, object], entity: str
    ) -> dict[str, object]:
        if not discovery.get("input_assets"):
            result = {"status": "SKIPPED", "metadata": {"entity": entity}}
        else:
            result = process_silver(
                source_type="CDC",
                storage_backend=os.environ.get("STORAGE_BACKEND", "minio"),
                input_prefix="cdc/",
                entity=entity,
                max_objects=int(os.environ.get("SILVER_MAX_OBJECTS", "100")),
            )
        _record(pipeline_run_id, f"process_{entity}", result)
        return result

    @task(task_id="run_quality_gate")
    def quality_gate(pipeline_run_id: str, results: list[dict[str, object]]) -> dict[str, object]:
        return finish_pipeline(
            pipeline_run_id=pipeline_run_id,
            results=results,
            warn_rate=SETTINGS.silver_warn_rate,
            fail_rate=SETTINGS.silver_fail_rate,
        )

    @task(task_id="publish_pipeline_result")
    def publish(result: dict[str, object]) -> dict[str, object]:
        return {"pipeline_run_id": result["pipeline_run_id"], "status": result["status"]}

    pipeline_run_id = start()
    discovery = discover_unprocessed_cdc_bronze(pipeline_run_id)
    customers = process_entity.override(task_id="process_customers")(
        pipeline_run_id, discovery, "customers"
    )
    merchants = process_entity.override(task_id="process_merchants")(
        pipeline_run_id, discovery, "merchants"
    )
    accounts = process_entity.override(task_id="process_accounts")(
        pipeline_run_id, discovery, "accounts"
    )
    payments = process_entity.override(task_id="process_payment_transactions")(
        pipeline_run_id, discovery, "payment_transactions"
    )
    events = process_entity.override(task_id="process_transaction_events")(
        pipeline_run_id, discovery, "transaction_events"
    )
    refunds = process_entity.override(task_id="process_refunds")(
        pipeline_run_id, discovery, "refunds"
    )

    customers >> accounts
    [customers, accounts, merchants] >> payments
    payments >> [events, refunds]
    gated = quality_gate(
        pipeline_run_id,
        [customers, accounts, merchants, payments, events, refunds],
    )
    [events, refunds] >> gated
    publish(gated)


def _record(pipeline_run_id: str, task_id: str, result: dict[str, object]) -> None:
    context = get_current_context()
    record_task_result(
        pipeline_run_id=pipeline_run_id,
        task_id=task_id,
        result=result,
        try_number=int(context["task_instance"].try_number),
    )


cdc_silver_processing_pipeline()
