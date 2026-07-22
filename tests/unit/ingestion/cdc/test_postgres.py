"""Tests for safe PostgreSQL CDC bootstrap configuration."""

import pytest

from common.config import ConfigurationError
from ingestion.cdc.config import CdcSettings
from ingestion.cdc.postgres import PostgresAdminSettings, bootstrap_postgres_cdc


class FakeResult:
    def __init__(self, row: tuple[object, ...] | None = None) -> None:
        self.row = row

    def fetchone(self) -> tuple[object, ...] | None:
        return self.row


class FakeConnection:
    def __init__(self, *, role_exists: bool, publication_exists: bool) -> None:
        self.role_exists = role_exists
        self.publication_exists = publication_exists
        self.calls: list[tuple[object, object]] = []

    def execute(self, query: object, params: object = None) -> FakeResult:
        call_number = len(self.calls)
        self.calls.append((query, params))
        if call_number == 0:
            return FakeResult((self.role_exists,))
        if call_number == 6:
            return FakeResult((self.publication_exists,))
        if call_number == 8:
            return FakeResult((True, False))
        return FakeResult()


def admin_settings() -> PostgresAdminSettings:
    return PostgresAdminSettings(
        host="postgres",
        port=5432,
        database="fintech_payments",
        user="payments_app",
        password="admin-test-password",
    )


def cdc_settings(*, database_user: str = "payments_cdc") -> CdcSettings:
    return CdcSettings(
        connect_url="http://kafka-connect:8083",
        connector_name="payments-postgres-cdc",
        database_host="postgres",
        database_port=5432,
        database_name="fintech_payments",
        database_user=database_user,
        database_password="cdc-test-password",
    )


def test_postgres_admin_settings_hide_password() -> None:
    settings = PostgresAdminSettings.from_env(
        {
            "POSTGRES_HOST": "postgres",
            "POSTGRES_PORT": "5432",
            "POSTGRES_DB": "fintech_payments",
            "POSTGRES_USER": "payments_app",
            "POSTGRES_PASSWORD": "admin-test-password",
        }
    )

    assert "admin-test-password" not in repr(settings)


def test_connector_role_must_not_reuse_bootstrap_administrator() -> None:
    with pytest.raises(ConfigurationError, match="must differ"):
        bootstrap_postgres_cdc(
            admin_settings(),
            cdc_settings(database_user="payments_app"),
        )


@pytest.mark.parametrize(
    ("role_exists", "publication_exists", "expected_created"),
    [(False, False, True), (True, True, False)],
)
def test_bootstrap_creates_or_reconciles_explicit_non_superuser_objects(
    role_exists: bool,
    publication_exists: bool,
    expected_created: bool,
) -> None:
    connection = FakeConnection(
        role_exists=role_exists,
        publication_exists=publication_exists,
    )

    result = bootstrap_postgres_cdc(
        admin_settings(),
        cdc_settings(),
        connection=connection,  # type: ignore[arg-type]
    )

    assert result.role_created is expected_created
    assert result.publication_created is expected_created
    assert result.captured_table_count == 6
    assert len(connection.calls) == 9


@pytest.mark.parametrize("port", ["not-a-number", "0", "65536"])
def test_invalid_postgres_bootstrap_port_is_rejected(port: str) -> None:
    with pytest.raises(ConfigurationError, match="POSTGRES_PORT"):
        PostgresAdminSettings.from_env(
            {
                "POSTGRES_HOST": "postgres",
                "POSTGRES_PORT": port,
                "POSTGRES_DB": "fintech_payments",
                "POSTGRES_USER": "payments_app",
                "POSTGRES_PASSWORD": "admin-test-password",
            }
        )
