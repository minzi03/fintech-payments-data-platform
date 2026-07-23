"""Acceptance tests for Phase 0 repository foundations."""

from pathlib import Path

import src
from common.project import (
    CURRENT_PHASE,
    FOUNDATION_PHASE,
    MINIMUM_PYTHON,
    PROJECT_NAME,
    PROJECT_SLUG,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_project_import() -> None:
    """Keep the root source package importable."""
    assert src is not None


def test_project_metadata() -> None:
    """Expose stable, portfolio-friendly project identity."""
    assert PROJECT_NAME == "Fintech Payments Data Platform"
    assert PROJECT_SLUG == "fintech-payments-data-platform"
    assert FOUNDATION_PHASE == 0
    assert CURRENT_PHASE == 7
    assert MINIMUM_PYTHON >= (3, 11)


def test_required_foundation_files_exist() -> None:
    """Protect the minimum Phase 0 repository contract."""
    required_files = (
        ".env.example",
        ".github/workflows/ci.yml",
        ".gitignore",
        "Makefile",
        "README.md",
        "docker-compose.yml",
        "docs/architecture/target-architecture.md",
        "docs/architecture/storage-abstraction.md",
        "docs/architecture/cdc-architecture.md",
        "docs/architecture/cdc-bronze-ingestion.md",
        "docs/business/business-case.md",
        "docs/business/requirements.md",
        "docs/data-model/source-model.md",
        "docs/roadmap.md",
        "docs/data-model/oltp-schema.md",
        "docs/data-model/settlement-contract.md",
        "docs/data-model/cdc-event-contract.md",
        "docs/data-model/cdc-bronze-schema.md",
        "docs/runbooks/local-postgres.md",
        "docs/runbooks/local-minio.md",
        "docs/runbooks/settlement-batch-ingestion.md",
        "docs/runbooks/local-kafka-debezium.md",
        "docs/runbooks/cdc-consumer.md",
        "docs/runbooks/cdc-recovery.md",
        "docs/architecture/silver-processing.md",
        "docs/data-model/silver-data-model.md",
        "docs/data-model/silver-quality-rules.md",
        "docs/runbooks/silver-processing.md",
        "docs/runbooks/silver-recovery.md",
        "docs/architecture/orchestration.md",
        "docs/architecture/control-plane.md",
        "docs/data-model/control-schema.md",
        "docs/runbooks/airflow-local.md",
        "docs/runbooks/pipeline-operations.md",
        "docs/runbooks/backfill.md",
        "docs/runbooks/orchestration-recovery.md",
        "contracts/batch/settlement_v1.yml",
        "infrastructure/postgres/init/001_create_database_objects.sql",
        "infrastructure/postgres/init/002_create_reference_data.sql",
        "infrastructure/postgres/init/003_create_indexes.sql",
        "infrastructure/debezium/connectors/payments-postgres.json",
        "infrastructure/airflow/Dockerfile",
        "infrastructure/airflow/init/001_create_control_schema.sql",
        "pyproject.toml",
        "src/__init__.py",
    )

    missing = [path for path in required_files if not (REPOSITORY_ROOT / path).is_file()]
    assert not missing, f"Missing required foundation files: {missing}"


def test_sensitive_example_values_are_safe_placeholders() -> None:
    """Keep local credentials explicit, recognizable, and non-production."""
    empty_future_keys = {
        "SNOWFLAKE_ACCOUNT",
        "SNOWFLAKE_PASSWORD",
        "SNOWFLAKE_USER",
    }
    values = {}

    for raw_line in (REPOSITORY_ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, value = line.split("=", maxsplit=1)
        values[key] = value

    assert empty_future_keys <= values.keys()
    assert all(values[key] == "" for key in empty_future_keys)
    assert values["MINIO_ACCESS_KEY"].startswith("change_me")
    assert values["MINIO_SECRET_KEY"].startswith("change_me")
    assert values["POSTGRES_USER"] == "payments_app"
    assert values["POSTGRES_PASSWORD"] == "change_me"
    assert values["DEBEZIUM_DATABASE_USER"] == "payments_cdc"
    assert values["DEBEZIUM_DATABASE_PASSWORD"].startswith("change_me")
    assert values["AIRFLOW_DATABASE_PASSWORD"].startswith("change_me")
    assert values["CONTROL_DATABASE_PASSWORD"].startswith("change_me")
    assert values["AIRFLOW_FERNET_KEY"].startswith("replace_with")
    assert values["AIRFLOW_SECRET_KEY"].startswith("replace_with")
    assert "change_me" in values["DATABASE_URL"]


def test_compose_file_has_only_phase_seven_services() -> None:
    """Phase 7 adds only its PostgreSQL-backed Airflow control plane."""
    compose_text = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "  postgres:" in compose_text
    assert "  minio:" in compose_text
    assert "  minio-init:" in compose_text
    assert "  kafka:" in compose_text
    assert "  kafka-connect:" in compose_text
    assert "  connector-init:" in compose_text
    assert "  cdc-consumer:" in compose_text
    assert "  airflow-postgres:" in compose_text
    assert "  airflow-init:" in compose_text
    assert "  airflow-webserver:" in compose_text
    assert "  airflow-scheduler:" in compose_text
    assert "  airflow-dag-processor:" in compose_text
    for forbidden_service in ("spark:", "flink:", "snowflake:", "dbt-runner:"):
        assert forbidden_service not in compose_text.lower()


def test_makefile_exposes_phase_zero_validation_targets() -> None:
    """Keep local and CI quality-gate names discoverable and consistent."""
    makefile = (REPOSITORY_ROOT / "Makefile").read_text(encoding="utf-8")
    required_targets = (
        "lint:",
        "format-check:",
        "test:",
        "yaml-check:",
        "compose-check:",
        "validate:",
        "postgres-up:",
        "postgres-down:",
        "postgres-logs:",
        "postgres-reset:",
        "generate-data:",
        "test-unit:",
        "test-integration:",
        "generate-settlement-fixtures:",
        "ingest-settlements:",
        "test-batch-unit:",
        "test-batch-integration:",
        "clean-runtime-data:",
        "minio-up:",
        "minio-down:",
        "minio-logs:",
        "minio-reset:",
        "test-minio-integration:",
        "ingest-settlements-minio:",
        "kafka-up:",
        "kafka-down:",
        "kafka-logs:",
        "connect-logs:",
        "cdc-up:",
        "cdc-down:",
        "cdc-status:",
        "cdc-register:",
        "cdc-delete:",
        "cdc-inspect:",
        "test-cdc-integration:",
        "cdc-consumer-run:",
        "cdc-consumer-once:",
        "cdc-consumer-logs:",
        "test-cdc-consumer-unit:",
        "test-cdc-consumer-integration:",
        "inspect-cdc-bronze:",
        "reset-cdc-consumer-state:",
        "silver-process-cdc:",
        "silver-process-settlements:",
        "silver-process-once:",
        "silver-inspect:",
        "test-silver-unit:",
        "test-silver-integration:",
        "reset-silver-state:",
        "airflow-build:",
        "airflow-init:",
        "airflow-up:",
        "airflow-down:",
        "airflow-logs:",
        "airflow-shell:",
        "airflow-demo-login-info:",
        "airflow-show-demo-password:",
        "airflow-dags-list:",
        "airflow-dag-test:",
        "test-airflow-unit:",
        "test-airflow-integration:",
        "trigger-settlement-pipeline:",
        "trigger-cdc-silver-pipeline:",
        "trigger-backfill:",
        "reset-airflow-metadata:",
    )
    assert all(target in makefile for target in required_targets)
    login_info_target = makefile.split("airflow-demo-login-info:", maxsplit=1)[1].split(
        "airflow-show-demo-password:", maxsplit=1
    )[0]
    assert "AIRFLOW_WEB_PORT" in login_info_target
    assert "AIRFLOW_ADMIN_USER" in login_info_target
    for forbidden_value in (
        "AIRFLOW_DATABASE_PASSWORD",
        "AIRFLOW_FERNET_KEY",
        "AIRFLOW_SECRET_KEY",
        "AIRFLOW_CONN_CONTROL_DB",
    ):
        assert forbidden_value not in login_info_target
    password_target = makefile.split("airflow-show-demo-password:", maxsplit=1)[1].split(
        "airflow-dags-list:", maxsplit=1
    )[0]
    assert '@test "$(CONFIRM)" = "1"' in password_target
    assert "SIMPLE_AUTH_MANAGER_PASSWORDS_FILE" in password_target
    assert "logs --tail" not in password_target
    backfill_target = makefile.split("trigger-backfill:", maxsplit=1)[1].split(
        "reset-airflow-metadata:", maxsplit=1
    )[0]
    assert '@test -n "$(BACKFILL_CONF)"' in backfill_target
    assert "provide a unique request_id" in backfill_target
