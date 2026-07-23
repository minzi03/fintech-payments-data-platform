from __future__ import annotations

from pathlib import Path

import pytest

DagBag = pytest.importorskip("airflow.dag_processing.dagbag").DagBag

LOCAL_DAG_FOLDER = Path(__file__).resolve().parents[1] / "dags"
DAG_FOLDER = Path("/opt/airflow/dags") if Path("/opt/airflow/dags").is_dir() else LOCAL_DAG_FOLDER
DAG_BAG = DagBag(dag_folder=str(DAG_FOLDER))


def test_settlement_pipeline_dependencies() -> None:
    dag = DAG_BAG.get_dag("settlement_batch_pipeline")
    assert dag is not None

    assert dag.get_task("discover_inbound_files").upstream_task_ids == {"start"}
    assert dag.get_task("ingest_settlement_files").upstream_task_ids == {
        "start",
        "discover_inbound_files",
    }
    assert dag.get_task("process_settlement_silver").upstream_task_ids == {
        "start",
        "discover_unprocessed_settlement_bronze",
    }
    assert dag.get_task("record_pipeline_success").upstream_task_ids == {
        "run_settlement_quality_gate"
    }


def test_cdc_silver_reference_dependencies() -> None:
    dag = DAG_BAG.get_dag("cdc_silver_processing_pipeline")
    assert dag is not None

    assert "process_customers" in dag.get_task("process_accounts").upstream_task_ids
    payments = dag.get_task("process_payment_transactions")
    assert {"process_customers", "process_accounts", "process_merchants"} <= (
        payments.upstream_task_ids
    )
    assert (
        "process_payment_transactions"
        in dag.get_task("process_transaction_events").upstream_task_ids
    )
    assert "process_payment_transactions" in dag.get_task("process_refunds").upstream_task_ids


def test_cdc_control_has_no_streaming_consumer_task() -> None:
    dag = DAG_BAG.get_dag("cdc_bronze_control")
    assert dag is not None

    assert "run_consumer" not in dag.task_ids
    assert {
        "check_postgres_cdc_prerequisites",
        "check_kafka_health",
        "check_kafka_connect_health",
        "check_consumer_group_lag",
        "check_cdc_manifest_freshness",
    } <= set(dag.task_ids)
