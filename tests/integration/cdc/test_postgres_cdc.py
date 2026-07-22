"""End-to-topic acceptance tests for the PostgreSQL Debezium connector."""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from ingestion.cdc.config import CAPTURED_TABLES, render_connector_definition
from ingestion.cdc.connect_api import EnsureAction
from ingestion.cdc.envelope import decimal_schema, decode_precise_decimal
from ingestion.cdc.inspection import (
    InspectedRecord,
    build_console_consumer_command,
    consume_topic,
)

from .conftest import CdcEnvironment

pytestmark = [pytest.mark.integration, pytest.mark.cdc_integration]

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CONNECTOR_TEMPLATE = REPOSITORY_ROOT / "infrastructure/debezium/connectors/payments-postgres.json"


@dataclass(frozen=True, slots=True)
class CdcProbe:
    lifecycle_customer_id: UUID
    deleted_customer_id: UUID
    account_id: UUID
    transaction_id: UUID
    event_ids: tuple[UUID, UUID]
    refund_id: UUID
    expected_amount: Decimal


@pytest.fixture(scope="module")
def cdc_probe(cdc_environment: CdcEnvironment) -> CdcProbe:
    connection = cdc_environment.connection
    suffix = uuid4().hex
    now = datetime.now(UTC)
    lifecycle_customer_id = uuid4()
    deleted_customer_id = uuid4()
    account_id = uuid4()
    merchant_id = uuid4()
    transaction_id = uuid4()
    requested_event_id = uuid4()
    completed_event_id = uuid4()
    refund_id = uuid4()
    expected_amount = Decimal("123456.78")

    connection.execute(
        """
        INSERT INTO payments.customers (
            customer_id, external_customer_ref, full_name, email, country_code, status
        ) VALUES (%s, %s, %s, NULL, 'VN', 'PENDING_VERIFICATION')
        """,
        (lifecycle_customer_id, f"CDC-LIFECYCLE-{suffix}", "CDC Integration Customer"),
    )
    connection.execute(
        "UPDATE payments.customers SET status = 'ACTIVE' WHERE customer_id = %s",
        (lifecycle_customer_id,),
    )
    _wait_for_key(
        cdc_environment,
        "customers",
        "customer_id",
        str(lifecycle_customer_id),
        {"c", "u"},
    )
    cdc_environment.client.restart()
    cdc_environment.client.wait_running()
    connection.execute(
        "UPDATE payments.customers SET status = 'SUSPENDED' WHERE customer_id = %s",
        (lifecycle_customer_id,),
    )

    connection.execute(
        """
        INSERT INTO payments.customers (
            customer_id, external_customer_ref, full_name, email, country_code, status
        ) VALUES (%s, %s, %s, NULL, 'VN', 'ACTIVE')
        """,
        (deleted_customer_id, f"CDC-DELETE-{suffix}", "CDC Delete Probe"),
    )
    connection.execute(
        "DELETE FROM payments.customers WHERE customer_id = %s",
        (deleted_customer_id,),
    )
    connection.execute(
        """
        INSERT INTO payments.accounts (
            account_id, customer_id, account_number, currency, balance, status
        ) VALUES (%s, %s, %s, 'USD', 200000.00, 'ACTIVE')
        """,
        (account_id, lifecycle_customer_id, f"CDC-{suffix[:24]}"),
    )
    connection.execute(
        "UPDATE payments.accounts SET balance = 199999.99 WHERE account_id = %s",
        (account_id,),
    )
    connection.execute(
        """
        INSERT INTO payments.merchants (
            merchant_id, merchant_code, external_reference, merchant_name,
            category_code, country_code, settlement_currency, status
        ) VALUES (%s, %s, %s, %s, '5812', 'VN', 'USD', 'ACTIVE')
        """,
        (
            merchant_id,
            f"CDC_{suffix[:20].upper()}",
            f"CDC-SETTLEMENT-{suffix}",
            "CDC Integration Merchant",
        ),
    )
    connection.execute(
        """
        INSERT INTO payments.payment_transactions (
            transaction_id, customer_id, account_id, merchant_id, transaction_type,
            payment_channel, amount, currency, status, partner_reference,
            idempotency_key, requested_at
        ) VALUES (%s, %s, %s, %s, 'MERCHANT_PAYMENT', 'CARD', %s, 'USD',
                  'PENDING', %s, %s, %s)
        """,
        (
            transaction_id,
            lifecycle_customer_id,
            account_id,
            merchant_id,
            expected_amount,
            f"CDC-PARTNER-{suffix}",
            f"CDC-IDEMPOTENCY-{suffix}",
            now,
        ),
    )
    connection.execute(
        """
        INSERT INTO payments.transaction_events (
            event_id, transaction_id, event_type, event_version, previous_status,
            new_status, event_time, producer_time, trace_id, source_system, event_payload
        ) VALUES (%s, %s, 'PAYMENT_REQUESTED', 1, NULL, 'PENDING', %s, %s, %s,
                  'cdc-integration-test', '{}'::jsonb)
        """,
        (requested_event_id, transaction_id, now, now, uuid4()),
    )
    completed_at = datetime.now(UTC)
    connection.execute(
        """
        UPDATE payments.payment_transactions
        SET status = 'COMPLETED', completed_at = %s
        WHERE transaction_id = %s
        """,
        (completed_at, transaction_id),
    )
    connection.execute(
        """
        INSERT INTO payments.transaction_events (
            event_id, transaction_id, event_type, event_version, previous_status,
            new_status, event_time, producer_time, trace_id, source_system, event_payload
        ) VALUES (%s, %s, 'PAYMENT_COMPLETED', 2, 'PENDING', 'COMPLETED', %s, %s, %s,
                  'cdc-integration-test', '{}'::jsonb)
        """,
        (completed_event_id, transaction_id, completed_at, completed_at, uuid4()),
    )
    connection.execute(
        """
        INSERT INTO payments.refunds (
            refund_id, transaction_id, amount, currency, status, reason_code,
            partner_reference, requested_at, completed_at
        ) VALUES (%s, %s, 10.25, 'USD', 'COMPLETED', 'CUSTOMER_REQUEST', %s, %s, %s)
        """,
        (refund_id, transaction_id, f"CDC-REFUND-{suffix}", completed_at, completed_at),
    )
    for table, key_name, key_value, operations in (
        ("customers", "customer_id", lifecycle_customer_id, {"c", "u"}),
        ("customers", "customer_id", deleted_customer_id, {"c", "d"}),
        ("accounts", "account_id", account_id, {"c", "u"}),
        ("payment_transactions", "transaction_id", transaction_id, {"c", "u"}),
        ("transaction_events", "event_id", completed_event_id, {"c"}),
        ("refunds", "refund_id", refund_id, {"c"}),
    ):
        _wait_for_key(
            cdc_environment,
            table,
            key_name,
            str(key_value),
            operations,
        )
    return CdcProbe(
        lifecycle_customer_id,
        deleted_customer_id,
        account_id,
        transaction_id,
        (requested_event_id, completed_event_id),
        refund_id,
        expected_amount,
    )


def test_services_connector_and_bootstrap_are_running_and_idempotent(
    cdc_environment: CdcEnvironment,
) -> None:
    definition = render_connector_definition(CONNECTOR_TEMPLATE, cdc_environment.settings)

    assert cdc_environment.client.ensure(definition) is EnsureAction.UNCHANGED
    assert cdc_environment.client.ensure(definition) is EnsureAction.UNCHANGED
    status = cdc_environment.client.wait_running()
    assert status["connector"]["state"] == "RUNNING"
    assert status["tasks"] and status["tasks"][0]["state"] == "RUNNING"


def test_initial_snapshot_and_exact_topic_set_exist(cdc_environment: CdcEnvironment) -> None:
    expected_topics = {cdc_environment.settings.topic_name(table) for table in CAPTURED_TABLES}
    observed_topics = _list_topics()
    snapshot_records = _records(cdc_environment, "payment_transactions")

    assert expected_topics <= observed_topics
    assert {
        topic for topic in observed_topics if topic.startswith("fintech.cdc.payments.")
    } == expected_topics
    assert any(record.envelope and record.envelope.op == "r" for record in snapshot_records)


def test_insert_update_and_restart_preserve_key_order(
    cdc_environment: CdcEnvironment,
    cdc_probe: CdcProbe,
) -> None:
    records = _matching_records(
        cdc_environment,
        "customers",
        "customer_id",
        str(cdc_probe.lifecycle_customer_id),
    )
    changes = [record for record in records if record.envelope is not None]

    assert [record.envelope.op for record in changes] == ["c", "u", "u"]
    assert len({record.partition for record in changes}) == 1
    assert [record.offset for record in changes] == sorted(record.offset for record in changes)
    assert all(record.envelope.source_lsn is not None for record in changes)


def test_delete_emits_delete_envelope_then_tombstone(
    cdc_environment: CdcEnvironment,
    cdc_probe: CdcProbe,
) -> None:
    records = _matching_records(
        cdc_environment,
        "customers",
        "customer_id",
        str(cdc_probe.deleted_customer_id),
    )

    assert any(record.envelope and record.envelope.op == "d" for record in records)
    assert records[-1].tombstone is True


def test_account_payment_event_and_refund_changes_are_emitted(
    cdc_environment: CdcEnvironment,
    cdc_probe: CdcProbe,
) -> None:
    expected = (
        ("accounts", "account_id", cdc_probe.account_id, {"c", "u"}),
        ("payment_transactions", "transaction_id", cdc_probe.transaction_id, {"c", "u"}),
        ("transaction_events", "event_id", cdc_probe.event_ids[0], {"c"}),
        ("transaction_events", "event_id", cdc_probe.event_ids[1], {"c"}),
        ("refunds", "refund_id", cdc_probe.refund_id, {"c"}),
    )
    for table, key_name, key_value, operations in expected:
        observed = {
            record.envelope.op
            for record in _matching_records(
                cdc_environment,
                table,
                key_name,
                str(key_value),
            )
            if record.envelope is not None
        }
        assert operations <= observed


def test_decimal_timestamp_and_source_metadata_are_lossless(
    cdc_environment: CdcEnvironment,
    cdc_probe: CdcProbe,
) -> None:
    documents = _raw_documents(cdc_environment, "payment_transactions")
    document = next(
        item
        for item in documents
        if item.get("payload", {}).get("after", {}).get("transaction_id")
        == str(cdc_probe.transaction_id)
        and item.get("payload", {}).get("op") == "c"
    )
    payload = document["payload"]
    amount_field = decimal_schema(document, "amount")
    scale = int(amount_field["parameters"]["scale"])
    amount = decode_precise_decimal(payload["after"]["amount"], scale)

    assert amount == cdc_probe.expected_amount
    assert amount_field["type"] == "bytes"
    assert amount_field["name"] == "org.apache.kafka.connect.data.Decimal"
    assert payload["after"]["requested_at"].endswith("Z")
    assert payload["source"]["lsn"] is not None
    assert payload["source"]["txId"] is not None
    assert payload["source"]["ts_us"] is not None


def _records(environment: CdcEnvironment, table: str) -> tuple[InspectedRecord, ...]:
    return consume_topic(
        topic=environment.settings.topic_name(table),
        max_messages=5_000,
        timeout_ms=1_500,
        compose_env=".env.example",
    )


def _matching_records(
    environment: CdcEnvironment,
    table: str,
    key_name: str,
    key_value: str,
) -> tuple[InspectedRecord, ...]:
    return tuple(
        record for record in _records(environment, table) if record.key == {key_name: key_value}
    )


def _wait_for_key(
    environment: CdcEnvironment,
    table: str,
    key_name: str,
    key_value: str,
    expected_operations: set[str],
) -> None:
    for _ in range(10):
        records = _matching_records(environment, table, key_name, key_value)
        observed = {record.envelope.op for record in records if record.envelope is not None}
        if expected_operations <= observed:
            return
        time.sleep(1)
    pytest.fail(f"CDC operations not observed for {table}: {sorted(expected_operations)}")


def _raw_documents(environment: CdcEnvironment, table: str) -> tuple[dict[str, Any], ...]:
    command = build_console_consumer_command(
        topic=environment.settings.topic_name(table),
        max_messages=5_000,
        timeout_ms=1_500,
        compose_env=".env.example",
    )
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    documents = []
    for line in result.stdout.splitlines():
        value = line.split("\t")[-1]
        if value == "null":
            continue
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict) and isinstance(parsed.get("payload"), dict):
            documents.append(parsed)
    return tuple(documents)


def _list_topics() -> set[str]:
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env.example",
            "exec",
            "-T",
            "kafka",
            "/opt/kafka/bin/kafka-topics.sh",
            "--bootstrap-server",
            "kafka:9092",
            "--list",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}
