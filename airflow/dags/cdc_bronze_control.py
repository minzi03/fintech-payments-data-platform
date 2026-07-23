"""Health and control checks for the continuously running CDC path."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow.sdk import dag, get_current_context, task

from orchestration.callbacks import failure_callback
from orchestration.config import OrchestrationSettings
from orchestration.health import (
    cdc_manifest_freshness,
    check_connector,
    check_kafka,
    check_postgres_logical_replication,
    consumer_group_lag,
)
from orchestration.tasks import begin_pipeline, finish_cdc_health_pipeline

SETTINGS = OrchestrationSettings.from_env(os.environ)


@dag(
    dag_id="cdc_bronze_control",
    description="Check CDC infrastructure, consumer lag, and Bronze manifest freshness.",
    schedule=SETTINGS.cdc_health_schedule,
    start_date=datetime(2026, 1, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    tags=["fintech", "cdc", "health", "bronze", "phase-7"],
    default_args={
        "owner": "data-platform",
        "retries": SETTINGS.task_retries,
        "retry_delay": timedelta(seconds=SETTINGS.retry_delay_seconds),
        "execution_timeout": timedelta(minutes=5),
        "on_failure_callback": failure_callback,
    },
    doc_md="""
    Model A: the CDC consumer remains a long-running service outside Airflow. This DAG
    performs bounded control checks only; Airflow is not used as a streaming engine.
    """,
)
def cdc_bronze_control():
    @task(task_id="start")
    def start() -> str:
        context = get_current_context()
        run_type = str(getattr(context["dag_run"].run_type, "value", "MANUAL")).upper()
        if run_type not in {"SCHEDULED", "MANUAL", "BACKFILL"}:
            run_type = "RECOVERY"
        return begin_pipeline(
            dag_id=context["dag"].dag_id,
            airflow_run_id=context["run_id"],
            pipeline_name="cdc-bronze-health-control",
            logical_date=context["logical_date"],
            run_type=run_type,
        )

    @task
    def check_postgres_cdc_prerequisites() -> dict[str, object]:
        return check_postgres_logical_replication(os.environ["DATABASE_URL"])

    @task
    def check_kafka_health() -> dict[str, object]:
        return check_kafka(_bootstrap_servers(), _topics())

    @task
    def check_kafka_connect_health() -> dict[str, object]:
        return check_connector(
            os.environ.get("KAFKA_CONNECT_URL", "http://kafka-connect:8083"),
            os.environ.get("DEBEZIUM_CONNECTOR_NAME", "payments-postgres-cdc"),
        )

    @task
    def check_consumer_group_lag() -> dict[str, object]:
        return consumer_group_lag(
            _bootstrap_servers(),
            os.environ.get("CDC_CONSUMER_GROUP_ID", "fintech-cdc-bronze-v1"),
            _topics(),
        )

    @task
    def check_cdc_manifest_freshness() -> dict[str, object]:
        return cdc_manifest_freshness(
            Path(
                os.environ.get(
                    "CDC_CONSUMER_MANIFEST_DB",
                    "/opt/airflow/project/data/cdc-consumer/cdc_consumer_manifest.sqlite3",
                )
            )
        )

    @task(task_id="record_health_result")
    def record_health(
        pipeline_run_id: str,
        postgres: dict[str, object],
        kafka: dict[str, object],
        connector: dict[str, object],
        lag: dict[str, object],
        freshness: dict[str, object],
    ) -> dict[str, object]:
        if not all(bool(item.get("healthy", True)) for item in (postgres, kafka, connector)):
            raise RuntimeError("A CDC dependency is unhealthy")
        return finish_cdc_health_pipeline(
            pipeline_run_id=pipeline_run_id,
            total_lag=int(lag["total_lag"]),
            freshness_seconds=float(freshness["freshness_seconds"]),
            lag_warn=SETTINGS.cdc_lag_warn,
            lag_fail=SETTINGS.cdc_lag_fail,
            freshness_warn=SETTINGS.cdc_freshness_warn_seconds,
            freshness_fail=SETTINGS.cdc_freshness_fail_seconds,
        )

    pipeline_run_id = start()
    postgres = check_postgres_cdc_prerequisites()
    kafka = check_kafka_health()
    connector = check_kafka_connect_health()
    lag = check_consumer_group_lag()
    freshness = check_cdc_manifest_freshness()
    record_health(pipeline_run_id, postgres, kafka, connector, lag, freshness)


def _bootstrap_servers() -> str:
    return os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "kafka:9092")


def _topics() -> tuple[str, ...]:
    return tuple(
        item.strip()
        for item in os.environ.get("CDC_CONSUMER_TOPICS", "").split(",")
        if item.strip()
    )


cdc_bronze_control()
