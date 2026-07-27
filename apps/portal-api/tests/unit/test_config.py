"""Portal API configuration safety tests."""

from datetime import UTC, datetime, timedelta

import pytest
from portal_api.core.config import PortalApiSettings, PortalEnvironment
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
        oidc_authorization_endpoint="https://identity.dev.example/authorize",
        oidc_token_endpoint="https://identity.dev.example/token",
        oidc_jwks_uri="https://identity.dev.example/jwks",
        oidc_redirect_uri="https://portal.dev.example/portal-api/v1/auth/callback",
    )
    assert settings.security_runtime_enabled
    assert settings.security_master_key_bytes == bytes(range(32))
    assert "secret" not in repr(settings)

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
            oidc_authorization_endpoint="https://identity.dev.example/authorize",
            oidc_token_endpoint="https://identity.dev.example/token",
            oidc_jwks_uri="https://identity.dev.example/jwks",
            oidc_redirect_uri="https://portal.dev.example/portal-api/v1/auth/callback",
        )

    with pytest.raises(ValidationError, match="decode to 256 bits"):
        PortalApiSettings(
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            security_master_key="dG9vLXNob3J0",
        )


def test_security_key_transition_is_complete_explicit_and_bounded() -> None:
    started_at = datetime(2026, 7, 27, 1, tzinfo=UTC)
    expires_at = started_at + timedelta(hours=1)
    settings = PortalApiSettings(
        environment=PortalEnvironment.TEST,
        security_runtime_enabled=True,
        database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
        security_master_key=TEST_SECURITY_MASTER_KEY,
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
            security_key_version="local-v2",
            security_previous_key_version="local-v1",
        )

    with pytest.raises(ValidationError, match="cannot exceed the absolute session lifetime"):
        PortalApiSettings(
            environment=PortalEnvironment.TEST,
            security_runtime_enabled=True,
            database_url="postgresql+psycopg://portal_runtime:secret@localhost/portal_control",
            security_master_key=TEST_SECURITY_MASTER_KEY,
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
