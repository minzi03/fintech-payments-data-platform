from __future__ import annotations

from pathlib import Path

import pytest

DagBag = pytest.importorskip("airflow.dag_processing.dagbag").DagBag

LOCAL_DAG_FOLDER = Path(__file__).resolve().parents[1] / "dags"
DAG_FOLDER = Path("/opt/airflow/dags") if Path("/opt/airflow/dags").is_dir() else LOCAL_DAG_FOLDER


@pytest.fixture(scope="module")
def dag_bag() -> DagBag:
    return DagBag(dag_folder=str(DAG_FOLDER))


def test_all_phase_seven_dags_parse_without_external_services(dag_bag: DagBag) -> None:
    assert not dag_bag.import_errors
    assert set(dag_bag.dags) == {
        "settlement_batch_pipeline",
        "cdc_bronze_control",
        "cdc_silver_processing_pipeline",
        "data_platform_backfill",
    }


@pytest.mark.parametrize(
    "dag_id",
    [
        "settlement_batch_pipeline",
        "cdc_bronze_control",
        "cdc_silver_processing_pipeline",
        "data_platform_backfill",
    ],
)
def test_dag_standards_are_explicit(dag_bag: DagBag, dag_id: str) -> None:
    dag = dag_bag.get_dag(dag_id)

    assert dag is not None
    assert dag.catchup is False
    assert dag.start_date is not None and dag.start_date.utcoffset() is not None
    assert dag.max_active_runs == 1
    assert {"fintech", "phase-7"} <= set(dag.tags)
    assert all(task.owner == "data-platform" for task in dag.tasks)
    assert all(task.retries >= 1 for task in dag.tasks)
    assert all(task.execution_timeout is not None for task in dag.tasks)


def test_no_forbidden_runtime_or_large_xcom_operator(dag_bag: DagBag) -> None:
    forbidden = ("Spark", "Flink", "Snowflake", "Dbt", "KubernetesPod", "Bash")
    for dag in dag_bag.dags.values():
        for task in dag.tasks:
            assert not any(name in type(task).__name__ for name in forbidden)
