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
    assert CURRENT_PHASE == 1
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
        "docs/business/business-case.md",
        "docs/business/requirements.md",
        "docs/data-model/source-model.md",
        "docs/roadmap.md",
        "docs/data-model/oltp-schema.md",
        "docs/runbooks/local-postgres.md",
        "infrastructure/postgres/init/001_create_database_objects.sql",
        "infrastructure/postgres/init/002_create_reference_data.sql",
        "infrastructure/postgres/init/003_create_indexes.sql",
        "pyproject.toml",
        "src/__init__.py",
    )

    missing = [path for path in required_files if not (REPOSITORY_ROOT / path).is_file()]
    assert not missing, f"Missing required foundation files: {missing}"


def test_sensitive_example_values_are_safe_placeholders() -> None:
    """Keep local credentials explicit, recognizable, and non-production."""
    empty_future_keys = {
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
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
    assert values["POSTGRES_USER"] == "payments_app"
    assert values["POSTGRES_PASSWORD"] == "change_me"
    assert "change_me" in values["DATABASE_URL"]


def test_compose_file_has_only_the_phase_one_postgres_service() -> None:
    """Prevent later-phase infrastructure from entering the Phase 1 Compose file."""
    compose_text = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "  postgres:" in compose_text
    for forbidden_service in ("kafka:", "minio:", "airflow:", "spark:", "debezium:"):
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
    )
    assert all(target in makefile for target in required_targets)
