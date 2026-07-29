"""Audit delivery ports and the deterministic local PostgreSQL destination."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import DBAPIError, OperationalError

from portal_api.audit.outbox_models import (
    AuditDeliveryMessage,
    DeliveryFailureClass,
    DeliveryResult,
    DeliveryResultKind,
)
from portal_api.db.metadata import audit_delivery_receipts
from portal_api.db.unit_of_work import local_transaction


class AuditDeliveryPort(Protocol):
    async def deliver(self, message: AuditDeliveryMessage) -> DeliveryResult: ...


@dataclass(frozen=True)
class PostgresReceiptDestination:
    """An append-only, idempotent local sink used for deterministic validation."""

    engine: Engine
    timeout_seconds: float

    async def deliver(self, message: AuditDeliveryMessage) -> DeliveryResult:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                return await asyncio.to_thread(self._deliver, message)
        except TimeoutError:
            return DeliveryResult(
                DeliveryResultKind.RETRYABLE_FAILURE,
                DeliveryFailureClass.TIMEOUT,
            )
        except OperationalError:
            return DeliveryResult(
                DeliveryResultKind.RETRYABLE_FAILURE,
                DeliveryFailureClass.CONNECTION,
            )
        except DBAPIError:
            return DeliveryResult(
                DeliveryResultKind.RETRYABLE_FAILURE,
                DeliveryFailureClass.DESTINATION_UNAVAILABLE,
            )
        except ValueError:
            return DeliveryResult(
                DeliveryResultKind.PERMANENT_FAILURE,
                DeliveryFailureClass.INVALID_PAYLOAD,
            )

    def _deliver(self, message: AuditDeliveryMessage) -> DeliveryResult:
        payload = message.payload_bytes
        checksum = hashlib.sha256(payload).hexdigest()
        statement = (
            pg_insert(audit_delivery_receipts)
            .values(
                idempotency_key=message.idempotency_key,
                event_id=message.audit_event_id,
                destination=message.destination.value,
                payload_checksum=checksum,
            )
            .on_conflict_do_nothing(index_elements=[audit_delivery_receipts.c.idempotency_key])
            .returning(audit_delivery_receipts.c.idempotency_key)
        )
        with local_transaction(self.engine) as connection:
            inserted = connection.execute(statement).scalar_one_or_none()
        kind = (
            DeliveryResultKind.SUCCESS
            if inserted is not None
            else DeliveryResultKind.ALREADY_DELIVERED
        )
        return DeliveryResult(
            kind,
            safe_reference=f"receipt:{message.idempotency_key}",
            checksum=checksum,
        )
