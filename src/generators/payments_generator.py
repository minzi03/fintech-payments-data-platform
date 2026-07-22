"""Deterministic, infrastructure-free fintech domain data generation."""

from __future__ import annotations

import random
from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid5

from generators.models import (
    CENT,
    Account,
    AccountStatus,
    Customer,
    CustomerStatus,
    EventType,
    GeneratedDataset,
    GeneratorConfig,
    Merchant,
    MerchantStatus,
    PaymentTransaction,
    Refund,
    RefundStatus,
    TransactionEvent,
    TransactionStatus,
    TransactionType,
)

GENERATOR_NAMESPACE = UUID("f1d29c24-b3bb-4d32-94e7-6cb6f882433a")
SUPPORTED_CURRENCIES = ("USD", "EUR", "GBP", "SGD")
COUNTRY_CODES = ("US", "GB", "SG", "DE", "VN")
MERCHANT_CATEGORIES = ("4814", "5411", "5732", "5812", "6012")
PAYMENT_CHANNELS = ("CARD", "QR", "WALLET")
FIRST_NAMES = ("Alex", "Casey", "Jordan", "Morgan", "Riley", "Taylor")
LAST_NAMES = ("Chen", "Garcia", "Nguyen", "Patel", "Smith", "Williams")
MERCHANT_WORDS = ("Atlas", "Beacon", "Harbor", "Lotus", "Northstar", "Summit")


def _stable_uuid(seed: int, entity: str, index: int) -> UUID:
    return uuid5(GENERATOR_NAMESPACE, f"{seed}:{entity}:{index}")


def _money_from_cents(cents: int) -> Decimal:
    return (Decimal(cents) / Decimal(100)).quantize(CENT)


class PaymentsGenerator:
    """Create one deterministic and internally consistent domain dataset."""

    def __init__(self, config: GeneratorConfig) -> None:
        self._config = config
        self._random = random.Random(config.seed)

    def generate(self) -> GeneratedDataset:
        """Generate customers, accounts, merchants, transactions, events, and refunds."""
        customers = self._generate_customers()
        accounts = self._generate_accounts(customers)
        merchants = self._generate_merchants()
        transactions, events = self._generate_transactions(accounts, merchants)
        refunds = self._generate_refunds(transactions)

        return GeneratedDataset(
            customers=tuple(customers),
            accounts=tuple(accounts),
            merchants=tuple(merchants),
            transactions=tuple(transactions),
            events=tuple(events),
            refunds=tuple(refunds),
            invalid_probe_count=self._config.probe_count(self._config.invalid_rate),
            duplicate_probe_count=self._config.probe_count(self._config.duplicate_rate),
        )

    def _generate_customers(self) -> list[Customer]:
        customers = []
        for index in range(self._config.customers):
            timestamp = self._config.base_time + timedelta(minutes=index)
            first_name = self._random.choice(FIRST_NAMES)
            last_name = self._random.choice(LAST_NAMES)
            customers.append(
                Customer(
                    customer_id=_stable_uuid(self._config.seed, "customer", index),
                    external_customer_ref=f"CUS-{self._config.seed}-{index:06d}",
                    full_name=f"{first_name} {last_name}",
                    email=f"customer.{self._config.seed}.{index}@example.test",
                    country_code=COUNTRY_CODES[index % len(COUNTRY_CODES)],
                    status=CustomerStatus.ACTIVE,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        return customers

    def _generate_accounts(self, customers: list[Customer]) -> list[Account]:
        accounts = []
        for index, customer in enumerate(customers):
            timestamp = customer.created_at + timedelta(seconds=10)
            currency = SUPPORTED_CURRENCIES[(index // 2) % len(SUPPORTED_CURRENCIES)]
            accounts.append(
                Account(
                    account_id=_stable_uuid(self._config.seed, "account", index),
                    customer_id=customer.customer_id,
                    account_number=f"FP{self._config.seed:06d}{index:010d}",
                    currency=currency,
                    balance=_money_from_cents(100_000 + self._random.randint(0, 900_000)),
                    status=AccountStatus.ACTIVE,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        return accounts

    def _generate_merchants(self) -> list[Merchant]:
        merchants = []
        for index in range(self._config.merchants):
            timestamp = self._config.base_time + timedelta(minutes=index, seconds=20)
            merchant_word = self._random.choice(MERCHANT_WORDS)
            merchants.append(
                Merchant(
                    merchant_id=_stable_uuid(self._config.seed, "merchant", index),
                    merchant_code=f"MER-{self._config.seed}-{index:06d}",
                    external_reference=f"MRC-{self._config.seed}-{index:06d}",
                    merchant_name=f"{merchant_word} Merchant {index + 1}",
                    category_code=MERCHANT_CATEGORIES[index % len(MERCHANT_CATEGORIES)],
                    country_code=COUNTRY_CODES[(index + 1) % len(COUNTRY_CODES)],
                    settlement_currency=SUPPORTED_CURRENCIES[index % len(SUPPORTED_CURRENCIES)],
                    status=MerchantStatus.ACTIVE,
                    created_at=timestamp,
                    updated_at=timestamp,
                )
            )
        return merchants

    def _generate_transactions(
        self,
        accounts: list[Account],
        merchants: list[Merchant],
    ) -> tuple[list[PaymentTransaction], list[TransactionEvent]]:
        accounts_by_currency: dict[str, list[Account]] = defaultdict(list)
        for account in accounts:
            accounts_by_currency[account.currency].append(account)

        transactions = []
        events = []
        for index in range(self._config.transactions):
            source_account = accounts[index % len(accounts)]
            same_currency_accounts = accounts_by_currency[source_account.currency]
            destinations = [
                account for account in same_currency_accounts if account != source_account
            ]
            is_transfer = index % 4 == 0 and bool(destinations)
            destination = destinations[index % len(destinations)] if is_transfer else None
            merchant = None if is_transfer else merchants[index % len(merchants)]
            requested_at = self._config.base_time + timedelta(
                minutes=index * 3,
                seconds=self._config.seed % 60,
            )
            status = self._transaction_status(index)
            terminal_at = requested_at + timedelta(seconds=5 + (index % 20))
            completed_at = terminal_at if status is TransactionStatus.COMPLETED else None
            failed_at = terminal_at if status is TransactionStatus.FAILED else None
            transaction = PaymentTransaction(
                transaction_id=_stable_uuid(self._config.seed, "transaction", index),
                customer_id=source_account.customer_id,
                account_id=source_account.account_id,
                destination_account_id=destination.account_id if destination else None,
                merchant_id=merchant.merchant_id if merchant else None,
                transaction_type=(
                    TransactionType.ACCOUNT_TRANSFER
                    if is_transfer
                    else TransactionType.MERCHANT_PAYMENT
                ),
                payment_channel=(
                    "BANK_TRANSFER"
                    if is_transfer
                    else PAYMENT_CHANNELS[index % len(PAYMENT_CHANNELS)]
                ),
                amount=_money_from_cents(self._random.randint(100, 500_000)),
                currency=source_account.currency,
                status=status,
                partner_reference=(
                    None
                    if status is TransactionStatus.PENDING
                    else f"PTR-{self._config.seed}-{index:08d}"
                ),
                idempotency_key=f"IDEM-{self._config.seed}-{index:08d}",
                failure_code="PROCESSOR_DECLINED" if status is TransactionStatus.FAILED else None,
                requested_at=requested_at,
                completed_at=completed_at,
                failed_at=failed_at,
                created_at=requested_at,
                updated_at=terminal_at if status is not TransactionStatus.PENDING else requested_at,
            )
            transactions.append(transaction)
            events.extend(self._events_for_transaction(transaction, index))

        return transactions, events

    @staticmethod
    def _transaction_status(index: int) -> TransactionStatus:
        outcome = index % 5
        if outcome == 0:
            return TransactionStatus.PENDING
        if outcome == 1:
            return TransactionStatus.FAILED
        return TransactionStatus.COMPLETED

    def _events_for_transaction(
        self,
        transaction: PaymentTransaction,
        transaction_index: int,
    ) -> list[TransactionEvent]:
        trace_id = _stable_uuid(self._config.seed, "trace", transaction_index)
        events = [
            self._event(
                transaction=transaction,
                transaction_index=transaction_index,
                event_sequence=0,
                event_type=EventType.PAYMENT_REQUESTED,
                previous_status=None,
                new_status=TransactionStatus.PENDING,
                event_time=transaction.requested_at,
                trace_id=trace_id,
            )
        ]

        if transaction.status is TransactionStatus.PENDING:
            return events

        previous_status = TransactionStatus.PENDING
        terminal_time = transaction.completed_at or transaction.failed_at
        if terminal_time is None:
            raise ValueError("terminal transaction is missing its terminal timestamp")

        if transaction.status is TransactionStatus.COMPLETED and transaction_index % 2 == 0:
            authorized_time = transaction.requested_at + timedelta(seconds=2)
            events.append(
                self._event(
                    transaction=transaction,
                    transaction_index=transaction_index,
                    event_sequence=1,
                    event_type=EventType.PAYMENT_AUTHORIZED,
                    previous_status=TransactionStatus.PENDING,
                    new_status=TransactionStatus.AUTHORIZED,
                    event_time=authorized_time,
                    trace_id=trace_id,
                )
            )
            previous_status = TransactionStatus.AUTHORIZED

        terminal_event_type = (
            EventType.PAYMENT_COMPLETED
            if transaction.status is TransactionStatus.COMPLETED
            else EventType.PAYMENT_FAILED
        )
        events.append(
            self._event(
                transaction=transaction,
                transaction_index=transaction_index,
                event_sequence=len(events),
                event_type=terminal_event_type,
                previous_status=previous_status,
                new_status=transaction.status,
                event_time=terminal_time,
                trace_id=trace_id,
            )
        )
        return events

    def _event(
        self,
        *,
        transaction: PaymentTransaction,
        transaction_index: int,
        event_sequence: int,
        event_type: EventType,
        previous_status: TransactionStatus | None,
        new_status: TransactionStatus,
        event_time: datetime,
        trace_id: UUID,
    ) -> TransactionEvent:
        producer_time = event_time + timedelta(milliseconds=100)
        return TransactionEvent(
            event_id=_stable_uuid(
                self._config.seed,
                f"transaction-event-{transaction_index}",
                event_sequence,
            ),
            transaction_id=transaction.transaction_id,
            event_type=event_type,
            event_version=event_sequence + 1,
            previous_status=previous_status,
            new_status=new_status,
            event_time=event_time,
            producer_time=producer_time,
            trace_id=trace_id,
            source_system="payments-generator",
            event_payload={
                "amount": str(transaction.amount),
                "currency": transaction.currency,
                "payment_channel": transaction.payment_channel,
                "status": new_status.value,
            },
            created_at=producer_time,
        )

    def _generate_refunds(self, transactions: list[PaymentTransaction]) -> list[Refund]:
        completed = [
            transaction
            for transaction in transactions
            if transaction.status is TransactionStatus.COMPLETED
        ]
        refunds = []
        for refund_index, transaction in enumerate(completed):
            if refund_index % 3 != 0:
                continue
            requested_at = (transaction.completed_at or transaction.requested_at) + timedelta(
                minutes=5
            )
            status = RefundStatus.COMPLETED if refund_index % 2 == 0 else RefundStatus.PENDING
            completed_at = (
                requested_at + timedelta(seconds=10) if status is RefundStatus.COMPLETED else None
            )
            amount = max(CENT, (transaction.amount * Decimal("0.25")).quantize(CENT))
            refunds.append(
                Refund(
                    refund_id=_stable_uuid(self._config.seed, "refund", refund_index),
                    transaction_id=transaction.transaction_id,
                    amount=amount,
                    currency=transaction.currency,
                    status=status,
                    reason_code="CUSTOMER_REQUEST",
                    partner_reference=(
                        f"RPR-{self._config.seed}-{refund_index:08d}"
                        if status is RefundStatus.COMPLETED
                        else None
                    ),
                    requested_at=requested_at,
                    completed_at=completed_at,
                    created_at=requested_at,
                    updated_at=completed_at or requested_at,
                )
            )
        return refunds
