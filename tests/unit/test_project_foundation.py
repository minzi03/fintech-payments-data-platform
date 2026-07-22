"""Acceptance tests for Phase 0 repository foundations."""

from pathlib import Path

import src
from common.project import FOUNDATION_PHASE, MINIMUM_PYTHON, PROJECT_NAME, PROJECT_SLUG

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_project_import() -> None:
    """Keep the root source package importable."""
    assert src is not None


def test_project_metadata() -> None:
    """Expose stable, portfolio-friendly project identity."""
    assert PROJECT_NAME == "Fintech Payments Data Platform"
    assert PROJECT_SLUG == "fintech-payments-data-platform"
    assert FOUNDATION_PHASE == 0
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
        "pyproject.toml",
        "src/__init__.py",
    )

    missing = [path for path in required_files if not (REPOSITORY_ROOT / path).is_file()]
    assert not missing, f"Missing required foundation files: {missing}"


def test_sensitive_example_values_are_empty() -> None:
    """Keep credential-bearing examples safe to commit."""
    sensitive_keys = {
        "MINIO_ACCESS_KEY",
        "MINIO_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "POSTGRES_USER",
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

    assert sensitive_keys <= values.keys()
    assert all(values[key] == "" for key in sensitive_keys)


def test_compose_file_has_no_phase_zero_services() -> None:
    """Prevent infrastructure implementations from entering Phase 0 accidentally."""
    compose_text = (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "services: {}" in compose_text


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
    )
    assert all(target in makefile for target in required_targets)
