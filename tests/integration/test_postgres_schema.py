"""Integration tests for PostgreSQL schema, constraints, and event immutability."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import Connection

from generators.models import GeneratorConfig
from generators.payments_generator import PaymentsGenerator
from generators.repositories import PaymentRepository, transaction_values

pytestmark = pytest.mark.integration

TRANSACTION_INSERT_SQL = """
    INSERT INTO payments.payment_transactions (
        transaction_id, customer_id, account_id, destination_account_id, merchant_id,
        transaction_type, payment_channel, amount, currency, status, partner_reference,
        idempotency_key, failure_code, requested_at, completed_at, failed_at,
        created_at, updated_at
    ) VALUES (
        %s, %s, %s, %s, %s, %s, %s, %s, %s,
        %s, %s, %s, %s, %s, %s, %s, %s, %s
    )
"""


def _persist_seed(connection: Connection[Any], seed: int = 7001):
    dataset = PaymentsGenerator(
        GeneratorConfig(seed=seed, customers=6, merchants=4, transactions=12)
    ).generate()
    PaymentRepository(connection).persist(dataset)
    return dataset


def _attempt_transaction(
    connection: Connection[Any],
    base_values: tuple[object, ...],
    *,
    amount: object | None = None,
    currency: str | None = None,
    status: str | None = None,
    idempotency_key: str | None = None,
) -> None:
    values = list(base_values)
    values[0] = uuid4()
    values[7] = values[7] if amount is None else amount
    values[8] = values[8] if currency is None else currency
    values[9] = values[9] if status is None else status
    values[10] = None
    values[11] = f"TEST-{uuid4().hex}" if idempotency_key is None else idempotency_key
    connection.execute(TRANSACTION_INSERT_SQL, values)


def test_required_tables_and_reference_data_exist(
    postgres_connection: Connection[Any],
) -> None:
    tables = {
        row[0]
        for row in postgres_connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'payments'
            """
        ).fetchall()
    }
    assert {
        "customers",
        "accounts",
        "merchants",
        "payment_transactions",
        "transaction_events",
        "refunds",
        "currencies",
        "payment_channels",
        "merchant_categories",
    } <= tables

    currencies = postgres_connection.execute(
        "SELECT COUNT(*) FROM payments.currencies WHERE is_active"
    ).fetchone()
    assert currencies is not None and currencies[0] >= 5


def test_required_primary_foreign_and_unique_constraints_exist(
    postgres_connection: Connection[Any],
) -> None:
    constraints = {
        row[0]
        for row in postgres_connection.execute(
            """
            SELECT constraint_name
            FROM information_schema.table_constraints
            WHERE constraint_schema = 'payments'
            """
        ).fetchall()
    }
    assert {
        "customers_pkey",
        "accounts_customer_id_fkey",
        "merchants_merchant_code_key",
        "merchants_external_reference_key",
        "payment_transactions_idempotency_key_key",
        "payment_transactions_partner_reference_key",
        "transaction_events_transaction_id_fkey",
        "transaction_events_transaction_version_unique",
        "refunds_transaction_id_fkey",
    } <= constraints


def test_account_foreign_key_rejects_unknown_customer(
    postgres_connection: Connection[Any],
) -> None:
    with (
        pytest.raises(psycopg.errors.ForeignKeyViolation),
        postgres_connection.transaction(),
    ):
        postgres_connection.execute(
            """
            INSERT INTO payments.accounts (
                account_id, customer_id, account_number, currency, balance, status
            ) VALUES (%s, %s, %s, 'USD', 100.00, 'ACTIVE')
            """,
            (uuid4(), uuid4(), f"TEST-{uuid4().hex[:20]}"),
        )


def test_transaction_checks_reject_invalid_amount_currency_and_status(
    postgres_connection: Connection[Any],
) -> None:
    dataset = _persist_seed(postgres_connection)
    base_values = transaction_values(dataset.transactions[2])

    with pytest.raises(psycopg.errors.CheckViolation), postgres_connection.transaction():
        _attempt_transaction(postgres_connection, base_values, amount="-1.00")

    with pytest.raises(psycopg.errors.ForeignKeyViolation), postgres_connection.transaction():
        _attempt_transaction(postgres_connection, base_values, currency="ZZZ")

    with pytest.raises(psycopg.errors.CheckViolation), postgres_connection.transaction():
        _attempt_transaction(postgres_connection, base_values, status="UNKNOWN")


def test_unique_idempotency_key_is_enforced(postgres_connection: Connection[Any]) -> None:
    dataset = _persist_seed(postgres_connection, seed=7002)
    base_values = transaction_values(dataset.transactions[2])

    with pytest.raises(psycopg.errors.UniqueViolation), postgres_connection.transaction():
        _attempt_transaction(
            postgres_connection,
            base_values,
            idempotency_key=dataset.transactions[2].idempotency_key,
        )


def test_transaction_events_insert_and_reject_mutation(
    postgres_connection: Connection[Any],
) -> None:
    dataset = _persist_seed(postgres_connection, seed=7003)
    event = dataset.events[0]
    stored_event = postgres_connection.execute(
        """
        SELECT event_type, event_time
        FROM payments.transaction_events
        WHERE event_id = %s
        """,
        (event.event_id,),
    ).fetchone()
    assert stored_event == (event.event_type.value, event.event_time)

    with (
        pytest.raises(psycopg.DatabaseError) as raised,
        postgres_connection.transaction(),
    ):
        postgres_connection.execute(
            "UPDATE payments.transaction_events SET event_version = 2 WHERE event_id = %s",
            (event.event_id,),
        )
    assert raised.value.sqlstate == "55000"
