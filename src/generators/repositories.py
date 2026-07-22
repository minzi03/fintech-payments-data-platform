"""PostgreSQL persistence for generated payment domain data."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

import psycopg
from psycopg import Connection, Cursor
from psycopg.types.json import Jsonb

from generators.models import GeneratedDataset, PaymentTransaction, Refund


class RefundRuleViolation(ValueError):
    """Raised when a refund violates an original transaction invariant."""


@dataclass(frozen=True, slots=True)
class PersistenceSummary:
    """Counts committed or deliberately rejected by one generator iteration."""

    customers: int
    accounts: int
    merchants: int
    transactions: int
    events: int
    refunds: int
    invalid_rejections: int
    duplicate_rejections: int


class PaymentRepository:
    """Persist a generated dataset within a caller-managed database transaction."""

    def __init__(self, connection: Connection[Any]) -> None:
        self._connection = connection

    def persist(self, dataset: GeneratedDataset) -> PersistenceSummary:
        """Insert valid domain records, then execute controlled rejection probes."""
        with self._connection.cursor() as cursor:
            self._insert_customers(cursor, dataset)
            self._insert_accounts(cursor, dataset)
            self._insert_merchants(cursor, dataset)
            self._insert_transactions(cursor, dataset)
            self._insert_events(cursor, dataset)

        for refund in dataset.refunds:
            self.insert_refund(refund)

        invalid_rejections = self._run_invalid_probes(dataset)
        duplicate_rejections = self._run_duplicate_probes(dataset)
        return PersistenceSummary(
            customers=len(dataset.customers),
            accounts=len(dataset.accounts),
            merchants=len(dataset.merchants),
            transactions=len(dataset.transactions),
            events=len(dataset.events),
            refunds=len(dataset.refunds),
            invalid_rejections=invalid_rejections,
            duplicate_rejections=duplicate_rejections,
        )

    @staticmethod
    def _insert_customers(cursor: Cursor[Any], dataset: GeneratedDataset) -> None:
        cursor.executemany(
            """
            INSERT INTO payments.customers (
                customer_id, external_customer_ref, full_name, email, country_code, status,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    customer.customer_id,
                    customer.external_customer_ref,
                    customer.full_name,
                    customer.email,
                    customer.country_code,
                    customer.status.value,
                    customer.created_at,
                    customer.updated_at,
                )
                for customer in dataset.customers
            ],
        )

    @staticmethod
    def _insert_accounts(cursor: Cursor[Any], dataset: GeneratedDataset) -> None:
        cursor.executemany(
            """
            INSERT INTO payments.accounts (
                account_id, customer_id, account_number, currency, balance, status,
                created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    account.account_id,
                    account.customer_id,
                    account.account_number,
                    account.currency,
                    account.balance,
                    account.status.value,
                    account.created_at,
                    account.updated_at,
                )
                for account in dataset.accounts
            ],
        )

    @staticmethod
    def _insert_merchants(cursor: Cursor[Any], dataset: GeneratedDataset) -> None:
        cursor.executemany(
            """
            INSERT INTO payments.merchants (
                merchant_id, merchant_code, external_reference, merchant_name, category_code,
                country_code, settlement_currency, status, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    merchant.merchant_id,
                    merchant.merchant_code,
                    merchant.external_reference,
                    merchant.merchant_name,
                    merchant.category_code,
                    merchant.country_code,
                    merchant.settlement_currency,
                    merchant.status.value,
                    merchant.created_at,
                    merchant.updated_at,
                )
                for merchant in dataset.merchants
            ],
        )

    @staticmethod
    def _insert_transactions(cursor: Cursor[Any], dataset: GeneratedDataset) -> None:
        cursor.executemany(
            """
            INSERT INTO payments.payment_transactions (
                transaction_id, customer_id, account_id, destination_account_id, merchant_id,
                transaction_type, payment_channel, amount, currency, status, partner_reference,
                idempotency_key, failure_code, requested_at, completed_at, failed_at,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            [transaction_values(transaction) for transaction in dataset.transactions],
        )

    @staticmethod
    def _insert_events(cursor: Cursor[Any], dataset: GeneratedDataset) -> None:
        cursor.executemany(
            """
            INSERT INTO payments.transaction_events (
                event_id, transaction_id, event_type, event_version, previous_status, new_status,
                event_time, producer_time, trace_id, source_system, event_payload, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    event.event_id,
                    event.transaction_id,
                    event.event_type.value,
                    event.event_version,
                    event.previous_status.value if event.previous_status else None,
                    event.new_status.value,
                    event.event_time,
                    event.producer_time,
                    event.trace_id,
                    event.source_system,
                    Jsonb(event.event_payload),
                    event.created_at,
                )
                for event in dataset.events
            ],
        )

    def insert_refund(self, refund: Refund) -> None:
        """Validate cross-row refund invariants under a transaction-row lock, then insert."""
        transaction_row = self._connection.execute(
            """
            SELECT amount, currency, status
            FROM payments.payment_transactions
            WHERE transaction_id = %s
            FOR UPDATE
            """,
            (refund.transaction_id,),
        ).fetchone()
        if transaction_row is None:
            raise RefundRuleViolation("original transaction does not exist")

        transaction_amount, transaction_currency, transaction_status = transaction_row
        if transaction_status != "COMPLETED":
            raise RefundRuleViolation("only completed transactions can be refunded")
        if transaction_currency != refund.currency:
            raise RefundRuleViolation("refund currency must match the original transaction")

        refunded_amount = self._connection.execute(
            """
            SELECT COALESCE(SUM(amount), 0.00)
            FROM payments.refunds
            WHERE transaction_id = %s
              AND status IN ('PENDING', 'COMPLETED')
            """,
            (refund.transaction_id,),
        ).fetchone()
        existing_total = refunded_amount[0] if refunded_amount else Decimal("0.00")
        if existing_total + refund.amount > transaction_amount:
            raise RefundRuleViolation(
                "total pending and completed refunds exceed transaction amount"
            )

        self._connection.execute(
            """
            INSERT INTO payments.refunds (
                refund_id, transaction_id, amount, currency, status, reason_code,
                partner_reference, requested_at, completed_at, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                refund.refund_id,
                refund.transaction_id,
                refund.amount,
                refund.currency,
                refund.status.value,
                refund.reason_code,
                refund.partner_reference,
                refund.requested_at,
                refund.completed_at,
                refund.created_at,
                refund.updated_at,
            ),
        )

    def _run_invalid_probes(self, dataset: GeneratedDataset) -> int:
        rejected = 0
        for index in range(dataset.invalid_probe_count):
            base = dataset.transactions[index % len(dataset.transactions)]
            try:
                with self._connection.transaction():
                    self._insert_probe_transaction(
                        base,
                        transaction_id=uuid5(base.transaction_id, f"invalid:{index}"),
                        idempotency_key=f"INVALID-PROBE-{base.idempotency_key}-{index}",
                        amount=Decimal("-1.00"),
                    )
                    raise RuntimeError("database accepted an invalid amount probe")
            except psycopg.IntegrityError as error:
                if error.diag.constraint_name != "payment_transactions_amount_positive":
                    raise
                rejected += 1
        return rejected

    def _run_duplicate_probes(self, dataset: GeneratedDataset) -> int:
        rejected = 0
        for index in range(dataset.duplicate_probe_count):
            base = dataset.transactions[index % len(dataset.transactions)]
            try:
                with self._connection.transaction():
                    self._insert_probe_transaction(
                        base,
                        transaction_id=uuid5(base.transaction_id, f"duplicate:{index}"),
                        idempotency_key=base.idempotency_key,
                        amount=base.amount,
                    )
                    raise RuntimeError("database accepted a duplicate idempotency key probe")
            except psycopg.IntegrityError as error:
                if error.diag.constraint_name != "payment_transactions_idempotency_key_key":
                    raise
                rejected += 1
        return rejected

    def _insert_probe_transaction(
        self,
        base: PaymentTransaction,
        *,
        transaction_id: UUID,
        idempotency_key: str,
        amount: Decimal,
    ) -> None:
        values = list(transaction_values(base))
        values[0] = transaction_id
        values[7] = amount
        values[10] = None
        values[11] = idempotency_key
        self._connection.execute(
            """
            INSERT INTO payments.payment_transactions (
                transaction_id, customer_id, account_id, destination_account_id, merchant_id,
                transaction_type, payment_channel, amount, currency, status, partner_reference,
                idempotency_key, failure_code, requested_at, completed_at, failed_at,
                created_at, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            values,
        )


def transaction_values(transaction: PaymentTransaction) -> tuple[object, ...]:
    """Convert one transaction to its stable SQL parameter order."""
    return (
        transaction.transaction_id,
        transaction.customer_id,
        transaction.account_id,
        transaction.destination_account_id,
        transaction.merchant_id,
        transaction.transaction_type.value,
        transaction.payment_channel,
        transaction.amount,
        transaction.currency,
        transaction.status.value,
        transaction.partner_reference,
        transaction.idempotency_key,
        transaction.failure_code,
        transaction.requested_at,
        transaction.completed_at,
        transaction.failed_at,
        transaction.created_at,
        transaction.updated_at,
    )
