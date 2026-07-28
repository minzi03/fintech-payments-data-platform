"""Lifecycle-managed at-least-once audit delivery worker and operator CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
from contextlib import suppress
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.engine import Engine

from portal_api.audit.delivery import AuditDeliveryPort, PostgresReceiptDestination
from portal_api.audit.maintenance import MaintenanceCoordinator
from portal_api.audit.outbox import AuditOutboxRepository
from portal_api.audit.outbox_models import (
    AuditDeliveryMessage,
    DeliveryFailureClass,
    DeliveryResult,
    DeliveryResultKind,
    OutboxBacklog,
    RetryPolicy,
    attempt_bucket,
)
from portal_api.core.config import PortalApiSettings, get_settings
from portal_api.core.logging import configure_logging
from portal_api.db.engine import create_audit_worker_engine
from portal_api.db.schema_guard import validate_runtime_schema
from portal_api.secret_provider import (
    ResolvedPortalSecrets,
    SecretProvider,
    environment_secret_provider,
    resolve_portal_secrets,
)
from portal_api.telemetry.metrics import TelemetryRecorder
from portal_api.telemetry.otel import build_telemetry

LOGGER = logging.getLogger("portal_api.audit.worker")


def _worker_health_status(
    *,
    outbox_available: bool,
    destination_available: bool,
    maintenance_overdue: int,
    backlog: OutboxBacklog,
    maximum_backoff_seconds: float,
) -> str:
    if not outbox_available or not destination_available:
        return "DOWN"
    if (
        maintenance_overdue > 0
        or backlog.dead_lettered > 0
        or backlog.oldest_pending_age_seconds > maximum_backoff_seconds * 2
    ):
        return "DEGRADED"
    return "UP"


class AuditOutboxWorker:
    """Claim briefly, deliver outside transactions, then finalize with fencing."""

    def __init__(
        self,
        *,
        repository: AuditOutboxRepository,
        destination: AuditDeliveryPort,
        maintenance: MaintenanceCoordinator,
        settings: PortalApiSettings,
        telemetry: TelemetryRecorder,
        worker_id: str | None = None,
    ) -> None:
        self._repository = repository
        self._destination = destination
        self._maintenance = maintenance
        self._settings = settings
        self._telemetry = telemetry
        self._worker_id = worker_id or f"portal-audit-{uuid4()}"
        self._retry_policy = RetryPolicy(
            base_delay_seconds=settings.audit_outbox_base_backoff_seconds,
            maximum_delay_seconds=settings.audit_outbox_max_backoff_seconds,
            jitter_ratio=0.1,
        )
        self._stopping = asyncio.Event()
        self._next_maintenance_at = 0.0

    async def run_once(self) -> int:
        with self._telemetry.span(
            "audit.outbox.claim_batch",
            attributes={
                "audit.destination": "local_postgres",
                "audit.batch_size": self._settings.audit_outbox_batch_size,
            },
        ):
            claims = await asyncio.to_thread(
                self._repository.claim_batch,
                worker_id=self._worker_id,
                batch_size=self._settings.audit_outbox_batch_size,
                lease_seconds=self._settings.audit_outbox_lease_seconds,
                max_attempts=self._settings.audit_outbox_max_attempts,
            )
        if claims:
            self._telemetry.record_outbox(
                operation="claimed",
                destination="local_postgres",
                result="claimed",
                failure_class="none",
                event_family="mixed",
                attempt_bucket="mixed",
                duration_ms=0,
                count=len(claims),
            )
        semaphore = asyncio.Semaphore(self._settings.audit_outbox_worker_concurrency)

        async def deliver(message: AuditDeliveryMessage) -> None:
            async with semaphore:
                started = perf_counter()
                try:
                    with self._telemetry.span(
                        "audit.outbox.deliver",
                        attributes={
                            "audit.destination": message.destination.value,
                            "audit.event_family": message.event_family,
                            "audit.attempt_bucket": attempt_bucket(message.attempt_count),
                        },
                    ):
                        result = await self._destination.deliver(message)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception(
                        "audit destination raised unexpectedly",
                        extra={
                            "event": "audit_destination_unexpected_failure",
                            "worker_id": self._worker_id,
                            "destination": message.destination.value,
                            "event_family": message.event_family,
                            "attempt_count": message.attempt_count,
                        },
                    )
                    result = DeliveryResult(
                        DeliveryResultKind.RETRYABLE_FAILURE,
                        DeliveryFailureClass.INTERNAL,
                    )
                duration_ms = (perf_counter() - started) * 1000
                operation = "delivery"
                if result.kind is DeliveryResultKind.RETRYABLE_FAILURE:
                    operation = (
                        "dead_lettered"
                        if message.attempt_count >= message.max_attempts
                        else "retry"
                    )
                elif result.kind is DeliveryResultKind.PERMANENT_FAILURE:
                    operation = "dead_lettered"
                with self._telemetry.span(
                    f"audit.outbox.{operation}",
                    attributes={
                        "audit.destination": message.destination.value,
                        "audit.result": result.kind.value,
                        "audit.event_family": message.event_family,
                        "audit.attempt_bucket": attempt_bucket(message.attempt_count),
                    },
                ):
                    finalized = await asyncio.to_thread(
                        self._repository.finalize,
                        message,
                        result,
                        retry_policy=self._retry_policy,
                    )
                self._telemetry.record_outbox(
                    operation=operation,
                    destination=message.destination.value,
                    result=result.kind.value.casefold(),
                    failure_class=result.failure_class.value,
                    event_family=message.event_family,
                    attempt_bucket=attempt_bucket(message.attempt_count),
                    duration_ms=duration_ms,
                )
                LOGGER.info(
                    "audit delivery finalized",
                    extra={
                        "event": "audit_delivery_finalized",
                        "worker_id": self._worker_id,
                        "destination": message.destination.value,
                        "event_family": message.event_family,
                        "result": result.kind.value,
                        "attempt_count": message.attempt_count,
                        "lease_state": "finalized" if finalized else "stale",
                    },
                )

        await asyncio.gather(*(deliver(message) for message in claims))
        backlog = await asyncio.to_thread(self._repository.backlog)
        self._telemetry.record_outbox_backlog(
            pending=backlog.pending + backlog.leased + backlog.retry_scheduled,
            dead_lettered=backlog.dead_lettered,
            oldest_pending_age_seconds=backlog.oldest_pending_age_seconds,
        )
        return len(claims)

    async def run(self) -> None:
        loop = asyncio.get_running_loop()
        self._next_maintenance_at = loop.time()
        self._maintenance.record_schedule_health(overdue=False)
        LOGGER.info(
            "audit outbox worker started",
            extra={
                "event": "audit_worker_started",
                "worker_id": self._worker_id,
                "batch_size": self._settings.audit_outbox_batch_size,
                "concurrency": self._settings.audit_outbox_worker_concurrency,
            },
        )
        database_failure_delay = self._settings.audit_outbox_base_backoff_seconds
        try:
            while not self._stopping.is_set():
                if (
                    self._settings.maintenance_enabled
                    and loop.time()
                    > self._next_maintenance_at + self._settings.maintenance_interval_seconds
                ):
                    self._maintenance.record_schedule_health(overdue=True)
                try:
                    delivered = await self.run_once()
                    database_failure_delay = self._settings.audit_outbox_base_backoff_seconds
                    if (
                        self._settings.maintenance_enabled
                        and loop.time() >= self._next_maintenance_at
                    ):
                        await asyncio.to_thread(self._maintenance.run_all)
                        self._next_maintenance_at = (
                            loop.time() + self._settings.maintenance_interval_seconds
                        )
                    delay = (
                        0.0
                        if delivered >= self._settings.audit_outbox_batch_size
                        else self._settings.audit_outbox_poll_interval_seconds
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.exception(
                        "audit worker iteration failed",
                        extra={
                            "event": "audit_worker_iteration_failed",
                            "worker_id": self._worker_id,
                            "failure_class": "database_or_internal",
                        },
                    )
                    delay = min(
                        database_failure_delay,
                        self._settings.audit_outbox_max_backoff_seconds,
                    )
                    database_failure_delay = min(
                        self._settings.audit_outbox_max_backoff_seconds,
                        max(
                            self._settings.audit_outbox_base_backoff_seconds,
                            database_failure_delay * 2,
                        ),
                    )
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._stopping.wait(), timeout=delay)
        finally:
            LOGGER.info(
                "audit outbox worker stopped",
                extra={"event": "audit_worker_stopped", "worker_id": self._worker_id},
            )

    def stop(self) -> None:
        self._stopping.set()


def _healthcheck(engine: Engine, settings: PortalApiSettings) -> int:
    validate_runtime_schema(engine)
    with engine.connect() as connection:
        outbox_available = connection.execute(
            text(
                "SELECT has_table_privilege("
                "current_user, 'portal_control.audit_archive_outbox', 'SELECT,UPDATE')"
            )
        ).scalar_one()
        destination_available = connection.execute(
            text(
                "SELECT has_table_privilege("
                "current_user, 'portal_control.audit_delivery_receipts', 'SELECT,INSERT')"
            )
        ).scalar_one()
        maintenance_overdue = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM portal_control.portal_maintenance_jobs "
                    "WHERE next_scheduled_at IS NOT NULL "
                    "AND next_scheduled_at < CURRENT_TIMESTAMP "
                    "- make_interval(secs => CAST(:grace AS double precision))"
                ),
                {"grace": settings.maintenance_interval_seconds},
            ).scalar_one()
        )
    repository = AuditOutboxRepository(engine)
    backlog = repository.backlog()
    status = _worker_health_status(
        outbox_available=bool(outbox_available),
        destination_available=bool(destination_available),
        maintenance_overdue=maintenance_overdue,
        backlog=backlog,
        maximum_backoff_seconds=settings.audit_outbox_max_backoff_seconds,
    )
    print(
        json.dumps(
            {
                "status": status,
                "database": "UP",
                "destination": "UP" if destination_available else "DOWN",
                "outbox": "UP" if outbox_available else "DOWN",
                "pending": backlog.pending + backlog.leased + backlog.retry_scheduled,
                "dead_lettered": backlog.dead_lettered,
                "maintenance_overdue": maintenance_overdue,
            },
            sort_keys=True,
        )
    )
    return 1 if status == "DOWN" else 0


async def _run(
    settings: PortalApiSettings,
    secrets: ResolvedPortalSecrets,
    *,
    once: bool,
) -> int:
    engine = create_audit_worker_engine(settings, secrets=secrets)
    validate_runtime_schema(engine)
    telemetry = build_telemetry(settings)
    telemetry.instrument_database(engine)
    telemetry.start()
    repository = AuditOutboxRepository(engine)
    worker_id = f"portal-audit-{uuid4()}"
    maintenance = MaintenanceCoordinator(
        engine=engine,
        repository=repository,
        settings=settings,
        telemetry=telemetry,
        worker_id=worker_id,
    )
    worker = AuditOutboxWorker(
        repository=repository,
        destination=PostgresReceiptDestination(
            engine=engine,
            timeout_seconds=settings.audit_outbox_delivery_timeout_seconds,
        ),
        maintenance=maintenance,
        settings=settings,
        telemetry=telemetry,
        worker_id=worker_id,
    )
    try:
        if once:
            await worker.run_once()
            if settings.maintenance_enabled:
                await asyncio.to_thread(maintenance.run_all)
            return 0
        loop = asyncio.get_running_loop()
        for signal_name in (signal.SIGINT, signal.SIGTERM):
            with suppress(NotImplementedError):
                loop.add_signal_handler(signal_name, worker.stop)
        await worker.run()
        return 0
    finally:
        telemetry.shutdown()
        engine.dispose()


def main(*, secret_provider: SecretProvider | None = None) -> int:
    parser = argparse.ArgumentParser(description="Portal audit outbox operator")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    parser.add_argument("--list-dead-letter", action="store_true")
    parser.add_argument("--requeue", type=UUID)
    arguments = parser.parse_args()
    settings = get_settings()
    configure_logging(settings)
    if not settings.audit_outbox_enabled:
        LOGGER.info(
            "audit outbox worker disabled",
            extra={"event": "audit_worker_disabled"},
        )
        return 0
    resolved_secret_provider = secret_provider or environment_secret_provider(settings)
    resolved_secret_provider.start()
    try:
        secrets = resolve_portal_secrets(settings, resolved_secret_provider)
    finally:
        resolved_secret_provider.close()
    LOGGER.info(
        "runtime secret references resolved",
        extra={
            "event": "secret_provider_resolved",
            **secrets.evidence.log_fields(),
        },
    )
    if arguments.healthcheck:
        engine = create_audit_worker_engine(settings, secrets=secrets)
        try:
            return _healthcheck(engine, settings)
        finally:
            engine.dispose()
    if arguments.list_dead_letter or arguments.requeue is not None:
        engine = create_audit_worker_engine(settings, secrets=secrets)
        try:
            repository = AuditOutboxRepository(engine)
            if arguments.requeue is not None:
                return 0 if repository.requeue(arguments.requeue) else 1
            for row in repository.dead_letters():
                print(
                    row["outbox_id"],
                    row["event_type"],
                    row["destination"],
                    row["attempt_count"],
                    row["last_error_class"],
                )
            return 0
        finally:
            engine.dispose()
    return asyncio.run(_run(settings, secrets, once=arguments.once))


if __name__ == "__main__":
    raise SystemExit(main())
