"""Portal API configuration safety tests."""

from datetime import UTC, datetime, timedelta

import pytest
from portal_api.core.config import (
    PortalApiSettings,
    PortalEnvironment,
    TelemetryMetricsExporter,
    TelemetryTraceExporter,
)
from pydantic import ValidationError

TEST_SECURITY_MASTER_KEY = "AAECAwQFBgcICQoLDA0ODxAREhMUFRYXGBkaGxwdHh8="
TEST_PREVIOUS_SECURITY_MASTER_KEY = "ICEiIyQlJicoKSorLC0uLzAxMjM0NTY3ODk6Ozw9Pj8="


def test_local_defaults_are_explicit_and_safe() -> None:
    settings = PortalApiSettings(_env_file=None)

    assert settings.environment is PortalEnvironment.LOCAL
    assert settings.service_version == "0.1.0-dev"
    assert settings.build_sha == "local"
    assert settings.allowed_origin_values == ("http://localhost:3000",)
    assert not settings.security_runtime_enabled
    assert settings.database_url is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("allowed_origins", "*", "explicit HTTP"),
        ("allowed_origins", "http://localhost:3000/path", "must not contain paths"),
        ("log_format", "xml", "json or console"),
        ("api_version", "v2", "must be v1"),
    ],
)
def test_invalid_configuration_is_rejected(field: str, value: object, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        PortalApiSettings(**{field: value})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"openapi_enabled": True}, "OPENAPI_ENABLED"),
        ({"log_format": "console"}, "LOG_FORMAT"),
        ({"development_identity_enabled": True}, "forbidden"),
        ({"trusted_hosts": "*"}, "Wildcard"),
        ({"allowed_origins": "http://portal.example"}, "HTTPS"),
        ({"build_sha": "local"}, "build SHA"),
    ],
)
def test_production_rejects_unsafe_defaults(overrides: dict[str, object], message: str) -> None:
    values: dict[str, object] = {
        "environment": PortalEnvironment.PRODUCTION,
        "openapi_enabled": False,
        "log_format": "json",
        "development_identity_enabled": False,
        "trusted_hosts": "portal-api.example",
        "allowed_origins": "https://portal.example",
        "build_sha": "abc123",
        "build_time": "2026-07-24T00:00:00Z",
    }
    values.update(overrides)

    with pytest.raises(ValidationError, match=message):
        PortalApiSettings(**values)


def test_production_configuration_can_be_valid() -> None:
    settings = PortalApiSettings(
        environment=PortalEnvironment.PRODUCTION,
        openapi_enabled=False,
        log_format="json",
        trusted_hosts="portal-api.example",
        allowed_origins="https://portal.example",
        build_sha="abc123",
        build_time="2026-07-24T00:00:00Z",
    )

    assert settings.is_production


def test_security_runtime_requires_postgresql_psycopg_url() -> None:
    with pytest.raises(ValidationError, match="DATABASE_URL is required"):
        PortalApiSettings(security_runtime_enabled=True)

    with pytest.raises(ValidationError, match="PostgreSQL with psycopg"):
        PortalApiSettings(
            security_runtime_enabled=True,
            database_url="sqlite:///portal.db",
        )


def test_security_runtime_is_bounded_to_local_and_development() -> None:
    settings = PortalApiSettings(
        environment=PortalEnvironment.DEVELOPMENT,
        security_runtime_enabled=True,
        database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
        security_master_key=TEST_SECURITY_MASTER_KEY,
        oidc_issuer="https://identity.dev.example/realms/portal",
        oidc_client_secret="test-client-secret",
        oidc_redirect_uri="https://portal.dev.example/portal-api/v1/auth/callback",
    )
    assert settings.security_runtime_enabled
    assert settings.security_master_key_bytes == bytes(range(32))
    assert "portal_runtime:secret" not in repr(settings)
    assert "test-client-secret" not in repr(settings)

    with pytest.raises(ValidationError, match="authorized only for local/development"):
        PortalApiSettings(
            environment=PortalEnvironment.STAGING,
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
        )


def test_restart_safe_security_runtime_requires_valid_master_key() -> None:
    with pytest.raises(ValidationError, match="SECURITY_MASTER_KEY is required"):
        PortalApiSettings(
            environment=PortalEnvironment.DEVELOPMENT,
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            oidc_issuer="https://identity.dev.example/realms/portal",
            oidc_client_secret="test-client-secret",
            oidc_redirect_uri="https://portal.dev.example/portal-api/v1/auth/callback",
        )

    with pytest.raises(ValidationError, match="decode to 256 bits"):
        PortalApiSettings(
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            security_master_key="dG9vLXNob3J0",
            oidc_client_secret="test-client-secret",
        )


def test_security_key_transition_is_complete_explicit_and_bounded() -> None:
    started_at = datetime(2026, 7, 27, 1, tzinfo=UTC)
    expires_at = started_at + timedelta(hours=1)
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        security_runtime_enabled=True,
        database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
        security_master_key=TEST_SECURITY_MASTER_KEY,
        oidc_client_secret="test-client-secret",
        security_key_version="local-v2",
        security_previous_master_key=TEST_PREVIOUS_SECURITY_MASTER_KEY,
        security_previous_key_version="local-v1",
        security_key_transition_started_at=started_at,
        security_key_transition_expires_at=expires_at,
    )

    assert settings.security_previous_master_key_bytes == bytes(range(32, 64))
    assert settings.security_key_transition_started_at == started_at
    assert settings.security_key_transition_expires_at == expires_at

    with pytest.raises(ValidationError, match="requires key, version, start, and expiry"):
        PortalApiSettings(
            environment=PortalEnvironment.TEST,
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            security_master_key=TEST_SECURITY_MASTER_KEY,
            oidc_client_secret="test-client-secret",
            security_key_version="local-v2",
            security_previous_key_version="local-v1",
        )

    with pytest.raises(ValidationError, match="cannot exceed the absolute session lifetime"):
        PortalApiSettings(
            environment=PortalEnvironment.TEST,
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            security_master_key=TEST_SECURITY_MASTER_KEY,
            oidc_client_secret="test-client-secret",
            security_key_version="local-v2",
            security_previous_master_key=TEST_PREVIOUS_SECURITY_MASTER_KEY,
            security_previous_key_version="local-v1",
            security_key_transition_started_at=started_at,
            security_key_transition_expires_at=started_at + timedelta(hours=9),
        )


def test_security_runtime_rejects_unsafe_oidc_configuration() -> None:
    with pytest.raises(ValidationError, match="must include openid"):
        PortalApiSettings(
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            oidc_scopes="profile",
        )

    with pytest.raises(ValidationError, match="local absolute paths"):
        PortalApiSettings(
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            allowed_return_paths="https://attacker.example",
        )

    with pytest.raises(ValidationError, match="OIDC_CLIENT_SECRET is required"):
        PortalApiSettings(
            _env_file=None,
            environment=PortalEnvironment.TEST,
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            oidc_client_secret=None,
        )


def test_oidc_discovery_and_cache_policy_are_frozen() -> None:
    settings = PortalApiSettings(
        oidc_issuer="https://identity.example/realms/portal",
        oidc_client_secret="test-client-secret",
    )

    assert settings.oidc_discovery_url_value == (
        "https://identity.example/realms/portal/.well-known/openid-configuration"
    )
    assert settings.oidc_cache_ttl_seconds == 900
    assert settings.oidc_stale_ceiling_seconds == 3600

    with pytest.raises(ValidationError):
        PortalApiSettings(oidc_cache_ttl_seconds=901)
    with pytest.raises(ValidationError):
        PortalApiSettings(oidc_stale_ceiling_seconds=3599)


def test_provider_session_lifecycle_configuration_is_bounded() -> None:
    settings = PortalApiSettings(
        provider_refresh_threshold_seconds=90,
        provider_refresh_scan_interval_seconds=2,
        provider_refresh_retry_budget=4,
        provider_refresh_initial_backoff_seconds=0.5,
        provider_refresh_max_backoff_seconds=20,
        provider_refresh_lease_seconds=31,
        provider_refresh_batch_size=50,
        provider_logout_timeout_seconds=6,
        provider_logout_replay_ttl_seconds=3600,
    )

    assert settings.provider_refresh_enabled
    assert settings.provider_refresh_threshold_seconds == 90
    assert settings.provider_refresh_batch_size == 50

    with pytest.raises(ValidationError, match="initial backoff"):
        PortalApiSettings(
            provider_refresh_initial_backoff_seconds=20,
            provider_refresh_max_backoff_seconds=10,
        )
    with pytest.raises(ValidationError, match="lease must exceed"):
        PortalApiSettings(
            oidc_http_timeout_seconds=15,
            provider_refresh_lease_seconds=15,
        )
    with pytest.raises(ValidationError):
        PortalApiSettings(provider_refresh_batch_size=101)


def test_telemetry_configuration_is_explicit_and_bounded() -> None:
    settings = PortalApiSettings(
        _env_file=None,
        telemetry_enabled=True,
        telemetry_metrics_exporter="otlp",
        telemetry_trace_exporter="otlp",
        telemetry_otlp_endpoint="https://collector.example",
        telemetry_trace_sampling_ratio=0.25,
        telemetry_resource_attributes="service.namespace=fintech,platform.region=local",
    )

    assert settings.telemetry_metrics_exporter is TelemetryMetricsExporter.OTLP
    assert settings.telemetry_trace_exporter is TelemetryTraceExporter.OTLP
    assert settings.telemetry_trace_sampling_ratio == 0.25
    assert settings.telemetry_resource_attribute_values == {
        "service.namespace": "fintech",
        "platform.region": "local",
    }

    with pytest.raises(ValidationError, match="at least one metrics or trace exporter"):
        PortalApiSettings(
            _env_file=None,
            telemetry_enabled=True,
            telemetry_metrics_exporter="none",
            telemetry_trace_exporter="none",
        )
    with pytest.raises(ValidationError, match="absolute HTTP"):
        PortalApiSettings(
            _env_file=None,
            telemetry_enabled=True,
            telemetry_metrics_exporter="otlp",
            telemetry_otlp_endpoint="collector:4318",
        )
    with pytest.raises(ValidationError, match="must not contain credentials"):
        PortalApiSettings(
            _env_file=None,
            telemetry_enabled=True,
            telemetry_metrics_exporter="otlp",
            telemetry_otlp_endpoint="https://username:password@collector.example",
        )
    with pytest.raises(ValidationError, match="bounded key=value"):
        PortalApiSettings(
            _env_file=None,
            telemetry_enabled=True,
            telemetry_resource_attributes="secret.token=must-not-be-a-resource",
        )


def test_production_otlp_requires_tls() -> None:
    with pytest.raises(ValidationError, match="OTLP export requires HTTPS"):
        PortalApiSettings(
            _env_file=None,
            environment=PortalEnvironment.PRODUCTION,
            openapi_enabled=False,
            log_format="json",
            trusted_hosts="portal-api.example",
            allowed_origins="https://portal.example",
            build_sha="abc123",
            build_time="2026-07-24T00:00:00Z",
            telemetry_enabled=True,
            telemetry_metrics_exporter="otlp",
            telemetry_otlp_endpoint="http://collector.internal:4318",
        )
