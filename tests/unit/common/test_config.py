"""Tests for safe environment-backed database configuration."""

import pytest

from common.config import (
    ConfigurationError,
    DatabaseSettings,
    MinioSettings,
    StorageBackendKind,
    StorageSettings,
)


def test_database_settings_from_individual_environment_variables() -> None:
    settings = DatabaseSettings.from_env(
        {
            "POSTGRES_HOST": "localhost",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "fintech_payments",
            "POSTGRES_USER": "payments_app",
            "POSTGRES_PASSWORD": "local-test-value",
        }
    )

    assert settings.host == "localhost"
    assert settings.port == 5432
    assert settings.connection_label == "localhost:5432/fintech_payments"
    assert "local-test-value" not in settings.connection_label


def test_database_url_is_accepted_without_exposing_credentials() -> None:
    settings = DatabaseSettings.from_env(
        {"DATABASE_URL": "postgresql://user:secret-value@db.example:5544/payments"}
    )

    assert settings.database_url is not None
    assert settings.connection_label == "db.example:5544/payments"
    assert "user" not in settings.connection_label
    assert "secret-value" not in settings.connection_label


def test_missing_database_configuration_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="Missing database configuration"):
        DatabaseSettings.from_env({})


@pytest.mark.parametrize("port", ["zero", "0", "65536"])
def test_invalid_database_port_is_rejected(port: str) -> None:
    environment = {
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": port,
        "POSTGRES_DB": "fintech_payments",
        "POSTGRES_USER": "payments_app",
        "POSTGRES_PASSWORD": "local-test-value",
    }

    with pytest.raises(ConfigurationError, match="POSTGRES_PORT"):
        DatabaseSettings.from_env(environment)


def _minio_environment() -> dict[str, str]:
    return {
        "STORAGE_BACKEND": "minio",
        "MINIO_ENDPOINT": "localhost:9000",
        "MINIO_ACCESS_KEY": "test-access-value",
        "MINIO_SECRET_KEY": "test-secret-value",
        "MINIO_SECURE": "false",
        "MINIO_REGION": "us-east-1",
        "MINIO_BRONZE_BUCKET": "fintech-bronze",
        "MINIO_QUARANTINE_BUCKET": "fintech-quarantine",
        "MINIO_MAX_RETRIES": "2",
    }


def test_local_storage_settings_do_not_require_minio_credentials() -> None:
    settings = StorageSettings.from_env({"STORAGE_BACKEND": "local"})

    assert settings.backend is StorageBackendKind.LOCAL
    assert settings.minio is None


def test_minio_settings_are_typed_and_hide_credentials() -> None:
    settings = StorageSettings.from_env(_minio_environment())

    assert settings.backend is StorageBackendKind.MINIO
    assert settings.minio is not None
    assert settings.minio.endpoint_label == "http://localhost:9000"
    assert "test-access-value" not in repr(settings.minio)
    assert "test-secret-value" not in repr(settings.minio)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("STORAGE_BACKEND", "azure", "local or minio"),
        ("MINIO_SECURE", "sometimes", "true or false"),
        ("MINIO_ENDPOINT", "http://user:password@localhost:9000", "credentials"),
        ("MINIO_ENDPOINT", "http://localhost:9000/path", "host and optional port"),
        ("MINIO_MAX_RETRIES", "11", "between 0 and 10"),
        ("MINIO_BRONZE_BUCKET", "INVALID_BUCKET", "valid 3-63"),
    ],
)
def test_invalid_storage_configuration_is_rejected(key: str, value: str, message: str) -> None:
    environment = _minio_environment()
    environment[key] = value

    with pytest.raises(ConfigurationError, match=message):
        StorageSettings.from_env(environment)


def test_minio_endpoint_scheme_must_match_secure_flag() -> None:
    environment = _minio_environment()
    environment["MINIO_ENDPOINT"] = "https://localhost:9000"

    with pytest.raises(ConfigurationError, match="must match"):
        MinioSettings.from_env(environment)
