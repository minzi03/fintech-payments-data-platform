"""Bounded, privacy-safe audit delivery domain models."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from random import Random
from typing import Any
from uuid import UUID

from portal_api.audit.redaction import assert_audit_safe

MAX_OUTBOX_PAYLOAD_BYTES = 64 * 1024


class OutboxStatus(StrEnum):
    PENDING = "PENDING"
    LEASED = "LEASED"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    DELIVERED = "DELIVERED"
    DEAD_LETTERED = "DEAD_LETTERED"
    CANCELLED = "CANCELLED"


class AuditDestination(StrEnum):
    LOCAL_POSTGRES = "local_postgres"


class DeliveryResultKind(StrEnum):
    SUCCESS = "SUCCESS"
    RETRYABLE_FAILURE = "RETRYABLE_FAILURE"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    ALREADY_DELIVERED = "ALREADY_DELIVERED"


class DeliveryFailureClass(StrEnum):
    NONE = "none"
    TIMEOUT = "timeout"
    CONNECTION = "connection"
    DESTINATION_UNAVAILABLE = "destination_unavailable"
    INVALID_PAYLOAD = "invalid_payload"
    PERMANENT_REJECTION = "permanent_rejection"
    INTERNAL = "internal"


@dataclass(frozen=True)
class AuditDeliveryMessage:
    outbox_id: UUID
    audit_event_id: UUID
    event_type: str
    event_family: str
    destination: AuditDestination
    payload_version: int
    payload: dict[str, Any]
    attempt_count: int
    max_attempts: int
    lease_token: UUID

    @property
    def idempotency_key(self) -> str:
        material = f"{self.audit_event_id}:{self.destination.value}:{self.payload_version}".encode()
        return hashlib.sha256(material).hexdigest()

    @property
    def payload_bytes(self) -> bytes:
        assert_audit_safe(self.payload, path="outbox.payload")
        encoded = json.dumps(
            self.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode()
        if len(encoded) > MAX_OUTBOX_PAYLOAD_BYTES:
            raise ValueError("Audit outbox payload exceeds the bounded delivery size")
        return encoded


@dataclass(frozen=True)
class DeliveryResult:
    kind: DeliveryResultKind
    failure_class: DeliveryFailureClass = DeliveryFailureClass.NONE
    safe_reference: str | None = None
    checksum: str | None = None


@dataclass(frozen=True)
class RetryPolicy:
    base_delay_seconds: float
    maximum_delay_seconds: float
    jitter_ratio: float = 0.0

    def delay(
        self,
        attempt_count: int,
        *,
        random_source: Random | None = None,
    ) -> timedelta:
        if attempt_count < 1:
            raise ValueError("Attempt count must be positive")
        base = min(
            self.maximum_delay_seconds,
            self.base_delay_seconds * (2 ** (attempt_count - 1)),
        )
        jitter = 0.0
        if self.jitter_ratio:
            source = random_source or Random()
            jitter = base * self.jitter_ratio * source.uniform(-1.0, 1.0)
        return timedelta(seconds=max(0.0, min(self.maximum_delay_seconds, base + jitter)))


@dataclass(frozen=True)
class OutboxBacklog:
    pending: int
    leased: int
    retry_scheduled: int
    dead_lettered: int
    oldest_pending_age_seconds: float
    observed_at: datetime

    @classmethod
    def empty(cls) -> OutboxBacklog:
        return cls(0, 0, 0, 0, 0.0, datetime.now(UTC))


def event_family(event_type: str) -> str:
    prefix = event_type.split(".", 1)[0]
    return prefix if prefix in {"auth", "authz", "security", "operations"} else "other"


def attempt_bucket(attempt_count: int) -> str:
    if attempt_count <= 1:
        return "1"
    if attempt_count <= 3:
        return "2-3"
    if attempt_count <= 5:
        return "4-5"
    return "6+"
