"""Typed domain models used by the deterministic payment generator."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

CENT = Decimal("0.01")
DEFAULT_BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)


class CustomerStatus(StrEnum):
    """Customer lifecycle states supported by the Phase 1 source."""

    PENDING_VERIFICATION = "PENDING_VERIFICATION"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


class AccountStatus(StrEnum):
    """Account lifecycle states supported by the Phase 1 source."""

    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    FROZEN = "FROZEN"
    CLOSED = "CLOSED"


class MerchantStatus(StrEnum):
    """Merchant lifecycle states supported by the Phase 1 source."""

    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    INACTIVE = "INACTIVE"
    CLOSED = "CLOSED"


class TransactionType(StrEnum):
    """Transaction business types in the OLTP source."""

    MERCHANT_PAYMENT = "MERCHANT_PAYMENT"
    ACCOUNT_TRANSFER = "ACCOUNT_TRANSFER"


class TransactionStatus(StrEnum):
    """Current payment transaction states."""

    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class EventType(StrEnum):
    """Immutable payment lifecycle event types."""

    PAYMENT_REQUESTED = "PAYMENT_REQUESTED"
    PAYMENT_AUTHORIZED = "PAYMENT_AUTHORIZED"
    PAYMENT_COMPLETED = "PAYMENT_COMPLETED"
    PAYMENT_FAILED = "PAYMENT_FAILED"


class RefundStatus(StrEnum):
    """Refund lifecycle states."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


@dataclass(frozen=True, slots=True)
class GeneratorConfig:
    """Validated generation controls independent from database configuration."""

    seed: int = 42
    customers: int = 10
    merchants: int = 5
    transactions: int = 50
    invalid_rate: float = 0.0
    duplicate_rate: float = 0.0
    base_time: datetime = DEFAULT_BASE_TIME

    def __post_init__(self) -> None:
        for field_name in ("customers", "merchants", "transactions"):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be at least 1")
        for field_name in ("invalid_rate", "duplicate_rate"):
            value = getattr(self, field_name)
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between 0.0 and 1.0")
        if self.base_time.tzinfo is None or self.base_time.utcoffset() is None:
            raise ValueError("base_time must be timezone-aware")

    def probe_count(self, rate: float) -> int:
        """Return a deterministic probe count, including one for any positive small rate."""
        if rate == 0.0:
            return 0
        return min(self.transactions, math.ceil(self.transactions * rate))


@dataclass(frozen=True, slots=True)
class Customer:
    """One current customer record."""

    customer_id: UUID
    external_customer_ref: str
    full_name: str
    email: str | None
    country_code: str
    status: CustomerStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Account:
    """One current account owned by one customer."""

    account_id: UUID
    customer_id: UUID
    account_number: str
    currency: str
    balance: Decimal
    status: AccountStatus
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.balance < Decimal("0.00"):
            raise ValueError("account balance must not be negative")


@dataclass(frozen=True, slots=True)
class Merchant:
    """One current merchant record."""

    merchant_id: UUID
    merchant_code: str
    external_reference: str
    merchant_name: str
    category_code: str
    country_code: str
    settlement_currency: str
    status: MerchantStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PaymentTransaction:
    """One payment transaction at its current state."""

    transaction_id: UUID
    customer_id: UUID
    account_id: UUID
    destination_account_id: UUID | None
    merchant_id: UUID | None
    transaction_type: TransactionType
    payment_channel: str
    amount: Decimal
    currency: str
    status: TransactionStatus
    partner_reference: str | None
    idempotency_key: str
    failure_code: str | None
    requested_at: datetime
    completed_at: datetime | None
    failed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.amount <= Decimal("0.00"):
            raise ValueError("transaction amount must be positive")


@dataclass(frozen=True, slots=True)
class TransactionEvent:
    """One immutable transaction lifecycle event."""

    event_id: UUID
    transaction_id: UUID
    event_type: EventType
    event_version: int
    previous_status: TransactionStatus | None
    new_status: TransactionStatus
    event_time: datetime
    producer_time: datetime
    trace_id: UUID
    source_system: str
    event_payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Refund:
    """One refund request at its current lifecycle state."""

    refund_id: UUID
    transaction_id: UUID
    amount: Decimal
    currency: str
    status: RefundStatus
    reason_code: str
    partner_reference: str | None
    requested_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    def __post_init__(self) -> None:
        if self.amount <= Decimal("0.00"):
            raise ValueError("refund amount must be positive")


@dataclass(frozen=True, slots=True)
class GeneratedDataset:
    """One deterministic, internally consistent generation result."""

    customers: tuple[Customer, ...]
    accounts: tuple[Account, ...]
    merchants: tuple[Merchant, ...]
    transactions: tuple[PaymentTransaction, ...]
    events: tuple[TransactionEvent, ...]
    refunds: tuple[Refund, ...]
    invalid_probe_count: int
    duplicate_probe_count: int
