"""Tests for safe environment-backed database configuration."""

import pytest

from common.config import ConfigurationError, DatabaseSettings


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
