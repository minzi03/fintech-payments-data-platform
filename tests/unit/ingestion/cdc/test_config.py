"""Tests for typed CDC settings and versioned connector rendering."""

from pathlib import Path

import pytest

from common.config import ConfigurationError
from ingestion.cdc.config import (
    CAPTURED_QUALIFIED_TABLES,
    CdcSettings,
    render_connector_definition,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
TEMPLATE = REPOSITORY_ROOT / "infrastructure/debezium/connectors/payments-postgres.json"


def cdc_environment() -> dict[str, str]:
    return {
        "KAFKA_CONNECT_URL": "http://localhost:8083",
        "KAFKA_TOPIC_PREFIX": "fintech.cdc",
        "KAFKA_DEFAULT_PARTITIONS": "3",
        "KAFKA_RETENTION_MS": "604800000",
        "DEBEZIUM_CONNECTOR_NAME": "payments-postgres-cdc",
        "DEBEZIUM_DATABASE_HOST": "postgres",
        "DEBEZIUM_DATABASE_PORT": "5432",
        "DEBEZIUM_DATABASE_NAME": "fintech_payments",
        "DEBEZIUM_DATABASE_USER": "payments_cdc",
        "DEBEZIUM_DATABASE_PASSWORD": "unit-test-password",
        "DEBEZIUM_SLOT_NAME": "fintech_payments_cdc_slot",
        "DEBEZIUM_PUBLICATION_NAME": "fintech_payments_cdc_publication",
        "DEBEZIUM_HEARTBEAT_INTERVAL_MS": "10000",
        "DEBEZIUM_SNAPSHOT_MODE": "initial",
    }


def test_settings_hide_password_and_derive_exact_topics() -> None:
    settings = CdcSettings.from_env(cdc_environment())

    assert "unit-test-password" not in repr(settings)
    assert settings.table_include_list == ",".join(CAPTURED_QUALIFIED_TABLES)
    assert settings.topic_name("payment_transactions") == (
        "fintech.cdc.payments.payment_transactions"
    )


def test_connector_template_preserves_envelope_and_precise_types() -> None:
    settings = CdcSettings.from_env(cdc_environment())
    definition = render_connector_definition(TEMPLATE, settings)

    assert definition.name == "payments-postgres-cdc"
    assert definition.config["table.include.list"] == settings.table_include_list
    assert definition.config["decimal.handling.mode"] == "precise"
    assert definition.config["decimal.handling.mode"] != "double"
    assert definition.config["time.precision.mode"] == "microseconds"
    assert definition.config["snapshot.mode"] == "initial"
    assert definition.config["heartbeat.interval.ms"] == "10000"
    assert definition.config["tombstones.on.delete"] == "true"
    assert definition.config["provide.transaction.metadata"] == "true"
    assert definition.config["value.converter.schemas.enable"] == "true"


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("DEBEZIUM_DATABASE_PASSWORD", "", "must not be empty"),
        ("DEBEZIUM_DATABASE_USER", "unsafe-user", "identifier"),
        ("DEBEZIUM_SLOT_NAME", "UPPERCASE", "identifier"),
        ("DEBEZIUM_SNAPSHOT_MODE", "always", "must be one of"),
        ("KAFKA_DEFAULT_PARTITIONS", "0", "between 1 and 100"),
        ("KAFKA_CONNECT_URL", "http://user:secret@localhost:8083", "credentials"),
    ],
)
def test_invalid_cdc_environment_is_rejected(key: str, value: str, message: str) -> None:
    environment = cdc_environment()
    environment[key] = value

    with pytest.raises(ConfigurationError, match=message):
        CdcSettings.from_env(environment)


def test_unknown_topic_table_is_rejected() -> None:
    settings = CdcSettings.from_env(cdc_environment())

    with pytest.raises(ConfigurationError, match="Unsupported CDC table"):
        settings.topic_name("settlement_records")


def test_partial_template_placeholder_is_rejected(tmp_path: Path) -> None:
    template = tmp_path / "connector.json"
    template.write_text(
        '{"name": "${DEBEZIUM_CONNECTOR_NAME}", "config": '
        '{"connector.class": "prefix-${KAFKA_TOPIC_PREFIX}"}}',
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="entire JSON string"):
        render_connector_definition(template, CdcSettings.from_env(cdc_environment()))
