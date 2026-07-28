"""Delivery-adapter failure classification, timeout, and cancellation tests."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from portal_api.audit.delivery import PostgresReceiptDestination
from portal_api.audit.outbox_models import (
    AuditDeliveryMessage,
    AuditDestination,
    DeliveryFailureClass,
    DeliveryResultKind,
)
from sqlalchemy.exc import OperationalError


def _message() -> AuditDeliveryMessage:
    event_id = uuid4()
    return AuditDeliveryMessage(
        outbox_id=event_id,
        audit_event_id=event_id,
        event_type="auth.login_started.v1",
        event_family="auth",
        destination=AuditDestination.LOCAL_POSTGRES,
        payload_version=1,
        payload={"schema_version": 1},
        attempt_count=1,
        max_attempts=5,
        lease_token=uuid4(),
    )


@pytest.mark.asyncio
async def test_delivery_timeout_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def delayed(*_: object, **__: object) -> object:
        await asyncio.sleep(1)
        return object()

    monkeypatch.setattr("portal_api.audit.delivery.asyncio.to_thread", delayed)
    destination = PostgresReceiptDestination(engine=object(), timeout_seconds=0.001)  # type: ignore[arg-type]

    result = await destination.deliver(_message())

    assert result.kind is DeliveryResultKind.RETRYABLE_FAILURE
    assert result.failure_class is DeliveryFailureClass.TIMEOUT


@pytest.mark.asyncio
async def test_connection_failure_is_retryable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unavailable(*_: object, **__: object) -> object:
        raise OperationalError("insert", {}, RuntimeError("database unavailable"))

    monkeypatch.setattr("portal_api.audit.delivery.asyncio.to_thread", unavailable)
    destination = PostgresReceiptDestination(engine=object(), timeout_seconds=1)  # type: ignore[arg-type]

    result = await destination.deliver(_message())

    assert result.kind is DeliveryResultKind.RETRYABLE_FAILURE
    assert result.failure_class is DeliveryFailureClass.CONNECTION


@pytest.mark.asyncio
async def test_invalid_payload_is_permanent(monkeypatch: pytest.MonkeyPatch) -> None:
    async def invalid(*_: object, **__: object) -> object:
        raise ValueError("invalid payload")

    monkeypatch.setattr("portal_api.audit.delivery.asyncio.to_thread", invalid)
    destination = PostgresReceiptDestination(engine=object(), timeout_seconds=1)  # type: ignore[arg-type]

    result = await destination.deliver(_message())

    assert result.kind is DeliveryResultKind.PERMANENT_FAILURE
    assert result.failure_class is DeliveryFailureClass.INVALID_PAYLOAD


@pytest.mark.asyncio
async def test_cancellation_propagates_without_false_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()

    async def blocked(*_: object, **__: object) -> object:
        started.set()
        await asyncio.Event().wait()
        return object()

    monkeypatch.setattr("portal_api.audit.delivery.asyncio.to_thread", blocked)
    destination = PostgresReceiptDestination(engine=object(), timeout_seconds=30)  # type: ignore[arg-type]
    task = asyncio.create_task(destination.deliver(_message()))
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
