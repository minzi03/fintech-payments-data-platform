"""Tests for deterministic and financially safe domain generation."""

from datetime import UTC
from decimal import Decimal

from generators.models import CENT, GeneratorConfig, TransactionStatus, TransactionType
from generators.payments_generator import PaymentsGenerator


def _dataset(seed: int = 2026):
    return PaymentsGenerator(
        GeneratorConfig(
            seed=seed,
            customers=6,
            merchants=4,
            transactions=15,
            invalid_rate=0.1,
            duplicate_rate=0.2,
        )
    ).generate()


def test_same_seed_produces_identical_domain_data() -> None:
    assert _dataset(77) == _dataset(77)
    assert _dataset(77) != _dataset(78)


def test_money_uses_decimal_with_two_fractional_digits() -> None:
    dataset = _dataset()
    monetary_values = [account.balance for account in dataset.accounts]
    monetary_values.extend(transaction.amount for transaction in dataset.transactions)
    monetary_values.extend(refund.amount for refund in dataset.refunds)

    assert monetary_values
    assert all(isinstance(value, Decimal) for value in monetary_values)
    assert all(value.quantize(CENT) == value for value in monetary_values)
    assert all(value.as_tuple().exponent == -2 for value in monetary_values)


def test_generated_timestamps_are_timezone_aware_utc() -> None:
    dataset = _dataset()
    timestamps = [customer.created_at for customer in dataset.customers]
    timestamps.extend(transaction.requested_at for transaction in dataset.transactions)
    timestamps.extend(event.event_time for event in dataset.events)
    timestamps.extend(refund.requested_at for refund in dataset.refunds)

    assert timestamps
    assert all(timestamp.tzinfo is not None for timestamp in timestamps)
    assert all(timestamp.utcoffset() == UTC.utcoffset(timestamp) for timestamp in timestamps)


def test_generator_creates_required_transaction_types_and_outcomes() -> None:
    dataset = _dataset()
    transaction_types = {transaction.transaction_type for transaction in dataset.transactions}
    statuses = {transaction.status for transaction in dataset.transactions}

    assert TransactionType.MERCHANT_PAYMENT in transaction_types
    assert TransactionType.ACCOUNT_TRANSFER in transaction_types
    assert TransactionStatus.PENDING in statuses
    assert TransactionStatus.COMPLETED in statuses
    assert TransactionStatus.FAILED in statuses


def test_transaction_events_represent_valid_status_transitions() -> None:
    dataset = _dataset()
    events_by_transaction = {
        transaction.transaction_id: [
            event for event in dataset.events if event.transaction_id == transaction.transaction_id
        ]
        for transaction in dataset.transactions
    }

    for transaction in dataset.transactions:
        events = events_by_transaction[transaction.transaction_id]
        assert [event.event_version for event in events] == list(range(1, len(events) + 1))
        assert events[0].previous_status is None
        assert events[0].new_status is TransactionStatus.PENDING
        assert events[-1].new_status is transaction.status
        if transaction.status is TransactionStatus.COMPLETED:
            assert events[-1].previous_status in {
                TransactionStatus.PENDING,
                TransactionStatus.AUTHORIZED,
            }
        if transaction.status is TransactionStatus.FAILED:
            assert events[-1].previous_status is TransactionStatus.PENDING


def test_refunds_never_exceed_the_original_transaction() -> None:
    dataset = _dataset()
    transactions = {transaction.transaction_id: transaction for transaction in dataset.transactions}

    assert dataset.refunds
    for refund in dataset.refunds:
        original = transactions[refund.transaction_id]
        assert original.status is TransactionStatus.COMPLETED
        assert Decimal("0.00") < refund.amount <= original.amount
        assert refund.currency == original.currency


def test_invalid_and_duplicate_rates_create_non_persisted_probe_counts() -> None:
    dataset = _dataset()

    assert dataset.invalid_probe_count == 2
    assert dataset.duplicate_probe_count == 3
