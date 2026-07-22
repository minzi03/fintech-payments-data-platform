"""Integration tests for one atomic generator persistence iteration."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from psycopg import Connection

from generators.models import GeneratorConfig
from generators.payments_generator import PaymentsGenerator
from generators.repositories import PaymentRepository, RefundRuleViolation

pytestmark = pytest.mark.integration


def test_generator_persists_related_domain_data_and_rejects_probes(
    postgres_connection: Connection[Any],
) -> None:
    dataset = PaymentsGenerator(
        GeneratorConfig(
            seed=8101,
            customers=6,
            merchants=4,
            transactions=15,
            invalid_rate=0.2,
            duplicate_rate=0.2,
        )
    ).generate()

    summary = PaymentRepository(postgres_connection).persist(dataset)

    assert summary.customers == 6
    assert summary.accounts == 6
    assert summary.merchants == 4
    assert summary.transactions == 15
    assert summary.events == len(dataset.events)
    assert summary.refunds == len(dataset.refunds)
    assert summary.invalid_rejections == 3
    assert summary.duplicate_rejections == 3

    stored_transactions = postgres_connection.execute(
        """
        SELECT COUNT(*)
        FROM payments.payment_transactions
        WHERE transaction_id = ANY(%s)
        """,
        ([transaction.transaction_id for transaction in dataset.transactions],),
    ).fetchone()
    assert stored_transactions == (15,)

    orphan_events = postgres_connection.execute(
        """
        SELECT COUNT(*)
        FROM payments.transaction_events event
        LEFT JOIN payments.payment_transactions transaction
          ON transaction.transaction_id = event.transaction_id
        WHERE transaction.transaction_id IS NULL
        """
    ).fetchone()
    assert orphan_events == (0,)


def test_repository_rejects_refund_total_above_original_amount(
    postgres_connection: Connection[Any],
) -> None:
    dataset = PaymentsGenerator(
        GeneratorConfig(seed=8102, customers=6, merchants=4, transactions=15)
    ).generate()
    repository = PaymentRepository(postgres_connection)
    repository.persist(dataset)
    original_refund = dataset.refunds[0]
    original_transaction = next(
        transaction
        for transaction in dataset.transactions
        if transaction.transaction_id == original_refund.transaction_id
    )
    oversized_refund = replace(
        original_refund,
        refund_id=uuid4(),
        amount=original_transaction.amount + Decimal("0.01"),
        partner_reference=None,
    )

    with pytest.raises(RefundRuleViolation, match="exceed transaction amount"):
        repository.insert_refund(oversized_refund)


def test_repository_rejects_refund_currency_mismatch(
    postgres_connection: Connection[Any],
) -> None:
    dataset = PaymentsGenerator(
        GeneratorConfig(seed=8103, customers=6, merchants=4, transactions=15)
    ).generate()
    repository = PaymentRepository(postgres_connection)
    repository.persist(dataset)
    mismatched_refund = replace(
        dataset.refunds[0],
        refund_id=uuid4(),
        currency="EUR" if dataset.refunds[0].currency != "EUR" else "USD",
        partner_reference=None,
    )

    with pytest.raises(RefundRuleViolation, match="currency"):
        repository.insert_refund(mismatched_refund)
