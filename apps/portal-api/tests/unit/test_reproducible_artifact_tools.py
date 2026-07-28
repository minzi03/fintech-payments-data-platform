"""S06-01 build-input and manifest safety tests."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def load_script(name: str) -> ModuleType:
    path = REPOSITORY_ROOT / "scripts" / "portal" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_portal_dependency_locks_cover_direct_requirements_and_hashes() -> None:
    locks = load_script("lock_portal_dependencies")

    runtime = locks.validate_lock(
        REPOSITORY_ROOT / "apps" / "portal-api" / "requirements.lock",
        include_dev=False,
    )
    development = locks.validate_lock(
        REPOSITORY_ROOT / "apps" / "portal-api" / "requirements-dev.lock",
        include_dev=True,
    )

    assert runtime
    assert development
    assert runtime.items() <= development.items()


def test_portal_dockerfiles_use_digest_pinned_base_images() -> None:
    artifacts = load_script("build_reproducible_artifacts")

    api = artifacts.pinned_base_images(REPOSITORY_ROOT / "apps" / "portal-api" / "Dockerfile")
    web = artifacts.pinned_base_images(REPOSITORY_ROOT / "apps" / "portal-web" / "Dockerfile")

    assert {item["stage"] for item in api} == {"dependencies", "runtime"}
    assert {item["stage"] for item in web} == {"dependencies", "build", "runtime"}
    external = [item for item in [*api, *web] if "reference" in item]
    assert all("@sha256:" in item["reference"] for item in external)
    assert "@sha256:" in artifacts.pinned_frontend(
        REPOSITORY_ROOT / "apps" / "portal-api" / "Dockerfile"
    )
    assert "@sha256:" in artifacts.pinned_frontend(
        REPOSITORY_ROOT / "apps" / "portal-web" / "Dockerfile"
    )


def test_portal_ci_actions_are_commit_pinned() -> None:
    artifacts = load_script("build_reproducible_artifacts")

    actions = artifacts.pinned_portal_actions(REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml")

    assert actions
    assert all(len(item["commit"]) == 40 for item in actions)


def test_manifest_safety_rejects_sensitive_fields_and_host_paths() -> None:
    artifacts = load_script("build_reproducible_artifacts")

    with pytest.raises(ValueError, match="sensitive field"):
        artifacts.manifest_has_sensitive_data({"registry_token": "redacted"})
    with pytest.raises(ValueError, match="host-specific path"):
        artifacts.manifest_has_sensitive_data({"path": "C:\\Users\\operator\\artifact"})

    artifacts.manifest_has_sensitive_data(
        {
            "schema_version": "portal-artifact-build/v1",
            "dockerfile": "apps/portal-api/Dockerfile",
            "digest": "sha256:" + ("a" * 64),
        }
    )
