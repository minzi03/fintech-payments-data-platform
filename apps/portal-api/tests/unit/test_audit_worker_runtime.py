"""Audit worker orchestration tests without a database or exporter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from portal_api.audit.outbox_models import (
    AuditDeliveryMessage,
    AuditDestination,
    DeliveryFailureClass,
    DeliveryResult,
    DeliveryResultKind,
    OutboxBacklog,
)
from portal_api.audit.worker import AuditOutboxWorker, _worker_health_status
from portal_api.core.config import PortalApiSettings
from portal_api.telemetry.metrics import InMemoryTelemetry


def _message(index: int) -> AuditDeliveryMessage:
    return AuditDeliveryMessage(
        outbox_id=uuid4(),
        audit_event_id=uuid4(),
        event_type=f"auth.login_started.v{index}",
        event_family="auth",
        destination=AuditDestination.LOCAL_POSTGRES,
        payload_version=1,
        payload={"schema_version": 1, "index": index},
        attempt_count=1,
        max_attempts=3,
        lease_token=uuid4(),
    )


@dataclass
class _Repository:
    messages: tuple[AuditDeliveryMessage, ...]
    finalized: list[tuple[AuditDeliveryMessage, DeliveryResult]] = field(default_factory=list)

    def claim_batch(self, **_: object) -> tuple[AuditDeliveryMessage, ...]:
        claimed, self.messages = self.messages, ()
        return claimed

    def finalize(
        self,
        message: AuditDeliveryMessage,
        result: DeliveryResult,
        **_: object,
    ) -> bool:
        self.finalized.append((message, result))
        return True

    def backlog(self) -> OutboxBacklog:
        return OutboxBacklog(0, 0, 0, 0, 0, datetime.now(UTC))


@dataclass
class _Destination:
    fail_index: int | None = None
    active: int = 0
    maximum_active: int = 0

    async def deliver(self, message: AuditDeliveryMessage) -> DeliveryResult:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(0)
            if message.payload["index"] == self.fail_index:
                raise RuntimeError("destination failure")
            return DeliveryResult(
                DeliveryResultKind.SUCCESS,
                safe_reference=f"receipt:{message.idempotency_key}",
                checksum="0" * 64,
            )
        finally:
            self.active -= 1


class _Maintenance:
    def run_all(self) -> tuple[object, ...]:
        return ()

    def record_schedule_health(self, *, overdue: bool) -> None:
        del overdue


def _settings(*, concurrency: int = 2) -> PortalApiSettings:
    return PortalApiSettings(
        _env_file=None,
        audit_outbox_worker_concurrency=concurrency,
        maintenance_enabled=False,
    )


def test_worker_health_mapping_is_bounded() -> None:
    healthy = OutboxBacklog.empty()
    dead_lettered = OutboxBacklog(0, 0, 0, 1, 0, datetime.now(UTC))

    assert (
        _worker_health_status(
            outbox_available=True,
            destination_available=True,
            maintenance_overdue=0,
            backlog=healthy,
            maximum_backoff_seconds=60,
        )
        == "UP"
    )
    assert (
        _worker_health_status(
            outbox_available=True,
            destination_available=True,
            maintenance_overdue=0,
            backlog=dead_lettered,
            maximum_backoff_seconds=60,
        )
        == "DEGRADED"
    )
    assert (
        _worker_health_status(
            outbox_available=False,
            destination_available=True,
            maintenance_overdue=0,
            backlog=healthy,
            maximum_backoff_seconds=60,
        )
        == "DOWN"
    )


@pytest.mark.asyncio
async def test_worker_delivers_claims_with_bounded_concurrency_and_records_telemetry() -> None:
    repository = _Repository(tuple(_message(index) for index in range(5)))
    destination = _Destination()
    telemetry = InMemoryTelemetry()
    worker = AuditOutboxWorker(
        repository=repository,  # type: ignore[arg-type]
        destination=destination,
        maintenance=_Maintenance(),  # type: ignore[arg-type]
        settings=_settings(concurrency=2),
        telemetry=telemetry,
        worker_id="unit-worker",
    )

    assert await worker.run_once() == 5
    assert len(repository.finalized) == 5
    assert all(result.kind is DeliveryResultKind.SUCCESS for _, result in repository.finalized)
    assert 1 <= destination.maximum_active <= 2
    snapshot = telemetry.snapshot()
    assert snapshot.archive_events["outbox:claimed:local_postgres:claimed:none:mixed:mixed"] == 5
    assert snapshot.archive_events["outbox:delivery:local_postgres:success:none:auth:1"] == 5


@pytest.mark.asyncio
async def test_worker_converts_unexpected_destination_exception_to_retryable_failure() -> None:
    repository = _Repository((_message(7),))
    destination = _Destination(fail_index=7)
    telemetry = InMemoryTelemetry()
    worker = AuditOutboxWorker(
        repository=repository,  # type: ignore[arg-type]
        destination=destination,
        maintenance=_Maintenance(),  # type: ignore[arg-type]
        settings=_settings(),
        telemetry=telemetry,
        worker_id="unit-worker",
    )

    assert await worker.run_once() == 1
    result = repository.finalized[0][1]
    assert result.kind is DeliveryResultKind.RETRYABLE_FAILURE
    assert result.failure_class is DeliveryFailureClass.INTERNAL
    assert (
        telemetry.snapshot().archive_events[
            "outbox:retry:local_postgres:retryable_failure:internal:auth:1"
        ]
        == 1
    )


@pytest.mark.asyncio
async def test_worker_cancellation_leaves_claim_for_lease_recovery() -> None:
    repository = _Repository((_message(9),))
    started = asyncio.Event()

    class BlockedDestination:
        async def deliver(self, message: AuditDeliveryMessage) -> DeliveryResult:
            del message
            started.set()
            await asyncio.Event().wait()
            return DeliveryResult(DeliveryResultKind.SUCCESS)

    worker = AuditOutboxWorker(
        repository=repository,  # type: ignore[arg-type]
        destination=BlockedDestination(),
        maintenance=_Maintenance(),  # type: ignore[arg-type]
        settings=_settings(),
        telemetry=InMemoryTelemetry(),
        worker_id="unit-worker",
    )
    task = asyncio.create_task(worker.run_once())
    await started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert repository.finalized == []


@pytest.mark.asyncio
async def test_worker_loop_backs_off_on_database_outage_and_stops_cleanly() -> None:
    class UnavailableRepository(_Repository):
        def claim_batch(self, **_: object) -> tuple[AuditDeliveryMessage, ...]:
            raise RuntimeError("database unavailable")

    worker = AuditOutboxWorker(
        repository=UnavailableRepository(()),  # type: ignore[arg-type]
        destination=_Destination(),
        maintenance=_Maintenance(),  # type: ignore[arg-type]
        settings=_settings(),
        telemetry=InMemoryTelemetry(),
        worker_id="unit-worker",
    )
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.01)
    worker.stop()

    await asyncio.wait_for(task, timeout=1)
