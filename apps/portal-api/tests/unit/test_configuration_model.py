"""Role-aware, immutable, drift-resistant configuration tests."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from portal_api.configuration import (
    ApiRuntimeConfiguration,
    AuditWorkerRuntimeConfiguration,
    MigrationEnvironmentInputs,
    PortalConfigurationError,
    PortalConfigurationWarning,
    PortalProcessRole,
)
from portal_api.core.config import PortalApiSettings, PortalEnvironment
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[4]
TEST_DATABASE_URL = "postgresql+psycopg://runtime:placeholder@database/portal"


def _production_values() -> dict[str, object]:
    return {
        "environment": PortalEnvironment.PRODUCTION,
        "openapi_enabled": False,
        "log_format": "json",
        "trusted_hosts": "portal-api.example",
        "allowed_origins": "https://portal.example",
        "build_sha": "abc123",
        "build_time": "2026-07-28T00:00:00Z",
    }


def test_flat_loader_and_role_aggregates_are_immutable() -> None:
    settings = PortalApiSettings(_env_file=None)
    api = settings.for_role(PortalProcessRole.API)

    assert isinstance(api, ApiRuntimeConfiguration)
    assert api.release.environment == "local"
    assert api.server.allowed_origins == ("http://localhost:3000",)
    assert api.secret_references.provider_id == "environment"
    assert "database_url" not in api.model_dump()

    with pytest.raises(ValidationError):
        settings.port = 9000  # type: ignore[misc]
    with pytest.raises(ValidationError):
        api.server.port = 9000  # type: ignore[misc]


def test_audit_worker_role_is_narrow_and_requires_enabled_outbox() -> None:
    with pytest.raises(PortalConfigurationError, match="ROLE_REQUIRED_FIELD"):
        PortalApiSettings(_env_file=None).for_role(PortalProcessRole.AUDIT_WORKER)

    settings = PortalApiSettings(
        _env_file=None,
        audit_outbox_enabled=True,
        audit_worker_database_url=TEST_DATABASE_URL,
    )
    worker = settings.for_role(PortalProcessRole.AUDIT_WORKER)

    assert isinstance(worker, AuditWorkerRuntimeConfiguration)
    assert worker.audit.enabled
    assert not hasattr(worker, "server")
    assert not hasattr(worker, "oidc")
    rendered = repr(worker.model_dump())
    assert "placeholder@" not in rendered


def test_environment_secret_inputs_are_isolated_from_runtime_aggregates() -> None:
    settings = PortalApiSettings(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
        oidc_client_secret="never-render-this",
    )

    inputs = settings.environment_secret_inputs
    assert inputs.configured_names == ("database_url", "oidc_client_secret")
    assert "never-render-this" not in repr(inputs)
    assert "placeholder@" not in repr(settings.for_role(PortalProcessRole.API))


def test_custom_provider_rejects_conflicting_inline_environment_secrets() -> None:
    with pytest.raises(ValidationError, match="reject conflicting inline"):
        PortalApiSettings(
            _env_file=None,
            secret_provider="external-agent",
            oidc_client_secret="must-not-be-ignored",
        )


def test_unknown_portal_variables_are_profile_aware_and_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PORTAL_API_UNKNOWN_DRIFT", "sensitive-value")

    with pytest.warns(PortalConfigurationWarning, match="PORTAL_API_UNKNOWN_DRIFT"):
        PortalApiSettings(_env_file=None, environment=PortalEnvironment.LOCAL)
    strict_profiles: tuple[tuple[PortalEnvironment, dict[str, object]], ...] = (
        (PortalEnvironment.TEST, {}),
        (
            PortalEnvironment.STAGING,
            {"allowed_origins": "https://portal.staging.example"},
        ),
        (PortalEnvironment.PRODUCTION, _production_values()),
    )
    for profile, overrides in strict_profiles:
        with pytest.raises(PortalConfigurationError) as failure:
            PortalApiSettings(
                _env_file=None,
                environment=profile,
                **{key: value for key, value in overrides.items() if key != "environment"},
            )

        rendered = str(failure.value)
        assert "PORTAL_CONFIG_UNKNOWN_VARIABLE" in rendered
        assert "PORTAL_API_UNKNOWN_DRIFT" in rendered
        assert "sensitive-value" not in rendered


def test_staging_reports_deferred_security_policies_without_authorizing_them() -> None:
    with pytest.warns(PortalConfigurationWarning) as captured:
        PortalApiSettings(
            _env_file=None,
            environment=PortalEnvironment.STAGING,
            allowed_origins="https://portal.staging.example",
        )

    rendered = "\n".join(str(item.message) for item in captured)
    assert "PORTAL_CONFIG_DEFERRED_CALLBACK_POLICY" in rendered
    assert "PORTAL_CONFIG_DEFERRED_ABUSE_POLICY" in rendered


def test_staging_applies_stricter_transport_validation() -> None:
    with pytest.raises(ValidationError, match="Staging CORS origins must use HTTPS"):
        PortalApiSettings(
            _env_file=None,
            environment=PortalEnvironment.STAGING,
            allowed_origins="http://portal.staging.example",
        )


def test_migration_configuration_is_separate_and_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PORTAL_MIGRATION_DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        MigrationEnvironmentInputs(_env_file=None)

    migration = MigrationEnvironmentInputs(
        _env_file=None,
        database_url=TEST_DATABASE_URL,
    )
    assert migration.runtime_configuration().role is PortalProcessRole.MIGRATION
    assert migration.reveal_validated_database_url() == TEST_DATABASE_URL
    assert "placeholder@" not in repr(migration)


def test_supported_api_aliases_are_synchronized_with_public_surfaces() -> None:
    expected = PortalApiSettings.supported_environment_aliases()
    surfaces = {
        ".env.example": ROOT / ".env.example",
        "docker-compose.yml": ROOT / "docker-compose.yml",
        "configuration.md": ROOT / "docs" / "portal" / "configuration.md",
    }

    for name, path in surfaces.items():
        names = set(re.findall(r"PORTAL_API_[A-Z0-9_]+", path.read_text(encoding="utf-8")))
        missing = sorted(expected - names)
        assert missing == [], f"{name} is missing supported aliases: {missing}"
