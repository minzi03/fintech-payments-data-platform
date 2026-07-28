"""Distributed, bounded maintenance jobs guarded by PostgreSQL advisory locks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.engine import Connection, Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.audit.outbox import AuditOutboxRepository
from portal_api.core.config import PortalApiSettings
from portal_api.db.metadata import portal_maintenance_jobs
from portal_api.telemetry.metrics import TelemetryRecorder

LOGGER = logging.getLogger("portal_api.audit.maintenance")

_JOB_LOCKS = {
    "recover_outbox_leases": 7_162_005_001,
    "cleanup_delivered_outbox": 7_162_005_002,
    "cleanup_expired_replays": 7_162_005_003,
    "cleanup_terminal_envelopes": 7_162_005_004,
}


@dataclass(frozen=True)
class MaintenanceResult:
    job_name: str
    status: str
    rows_processed: int
    duration_ms: float


class MaintenanceCoordinator:
    """Run each job once per cluster without deleting active authority."""

    def __init__(
        self,
        *,
        engine: Engine,
        repository: AuditOutboxRepository,
        settings: PortalApiSettings,
        telemetry: TelemetryRecorder,
        worker_id: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._engine = engine
        self._repository = repository
        self._settings = settings
        self._telemetry = telemetry
        self._worker_id = worker_id
        self._clock = clock or (lambda: datetime.now(UTC))
        self._audit = AuditLedger()

    def run_all(self) -> tuple[MaintenanceResult, ...]:
        now = self._clock()
        delivered_before = now - timedelta(days=self._settings.audit_outbox_retention_days)
        replay_before = now - timedelta(seconds=self._settings.replay_retention_buffer_seconds)
        envelope_before = now - timedelta(days=self._settings.terminal_envelope_retention_days)
        jobs: tuple[tuple[str, Callable[[], int]], ...] = (
            (
                "recover_outbox_leases",
                lambda: self._repository.recover_expired_leases(
                    batch_size=self._settings.maintenance_batch_size
                ),
            ),
            (
                "cleanup_delivered_outbox",
                lambda: self._repository.cleanup_delivered(
                    older_than=delivered_before,
                    batch_size=self._settings.maintenance_batch_size,
                ),
            ),
            (
                "cleanup_expired_replays",
                lambda: self._repository.cleanup_expired_replays(
                    older_than=replay_before,
                    batch_size=self._settings.maintenance_batch_size,
                ),
            ),
            (
                "cleanup_terminal_envelopes",
                lambda: self._repository.cleanup_terminal_envelopes(
                    older_than=envelope_before,
                    batch_size=self._settings.maintenance_batch_size,
                ),
            ),
        )
        results: list[MaintenanceResult] = []
        for name, action in jobs:
            with self._telemetry.span(
                "portal.maintenance.run",
                attributes={"maintenance.job_name": name},
            ):
                results.append(self._run_job(name, action))
            self._telemetry.record_maintenance_overdue(job_name=name, overdue=False)
        return tuple(results)

    def record_schedule_health(self, *, overdue: bool) -> None:
        for job_name in _JOB_LOCKS:
            self._telemetry.record_maintenance_overdue(
                job_name=job_name,
                overdue=overdue,
            )

    def _run_job(
        self,
        job_name: str,
        action: Callable[[], int],
    ) -> MaintenanceResult:
        started = perf_counter()
        run_id = uuid4()
        next_run = self._clock() + timedelta(seconds=self._settings.maintenance_interval_seconds)
        try:
            with self._engine.begin() as lock_connection:
                acquired = bool(
                    lock_connection.execute(
                        text("SELECT pg_try_advisory_xact_lock(:key)"),
                        {"key": _JOB_LOCKS[job_name]},
                    ).scalar_one()
                )
                if not acquired:
                    duration_ms = (perf_counter() - started) * 1000
                    self._telemetry.record_maintenance(
                        job_name=job_name,
                        status="overlap_skipped",
                        rows_processed=0,
                        duration_ms=duration_ms,
                        failure_class="none",
                    )
                    LOGGER.info(
                        "maintenance overlap skipped",
                        extra={
                            "event": "maintenance_overlap_skipped",
                            "job_name": job_name,
                            "worker_id": self._worker_id,
                            "status": "overlap_skipped",
                            "rows_processed": 0,
                        },
                    )
                    return MaintenanceResult(
                        job_name,
                        "OVERLAP_SKIPPED",
                        0,
                        duration_ms,
                    )
                previous_status = lock_connection.execute(
                    select(portal_maintenance_jobs.c.status).where(
                        portal_maintenance_jobs.c.job_name == job_name
                    )
                ).scalar_one_or_none()
                self._repository.update_job_checkpoint(
                    connection=lock_connection,
                    job_name=job_name,
                    status="RUNNING",
                    worker_id=self._worker_id,
                    run_id=run_id,
                    rows_processed=0,
                    next_scheduled_at=next_run,
                )
                rows_processed = action()
                elapsed = perf_counter() - started
                if elapsed > self._settings.maintenance_max_runtime_seconds:
                    raise TimeoutError("Maintenance job exceeded its bounded runtime")
                self._repository.update_job_checkpoint(
                    connection=lock_connection,
                    job_name=job_name,
                    status="SUCCEEDED",
                    worker_id=self._worker_id,
                    run_id=run_id,
                    rows_processed=rows_processed,
                    next_scheduled_at=next_run,
                )
                if rows_processed:
                    self._append_event(
                        lock_connection,
                        AuditEventType.MAINTENANCE_DATA_REMOVED,
                        reason_code=job_name,
                        safe_metadata={
                            "job_name": job_name,
                            "rows_processed": rows_processed,
                        },
                    )
                if previous_status == "FAILED":
                    self._append_event(
                        lock_connection,
                        AuditEventType.MAINTENANCE_JOB_RECOVERED,
                        reason_code=job_name,
                        safe_metadata={"job_name": job_name},
                    )
            duration_ms = (perf_counter() - started) * 1000
            self._telemetry.record_maintenance(
                job_name=job_name,
                status="succeeded",
                rows_processed=rows_processed,
                duration_ms=duration_ms,
                failure_class="none",
            )
            LOGGER.info(
                "maintenance job completed",
                extra={
                    "event": "maintenance_job_completed",
                    "job_name": job_name,
                    "worker_id": self._worker_id,
                    "status": "succeeded",
                    "rows_processed": rows_processed,
                    "duration_ms": round(duration_ms, 3),
                },
            )
            return MaintenanceResult(job_name, "SUCCEEDED", rows_processed, duration_ms)
        except Exception:
            duration_ms = (perf_counter() - started) * 1000
            LOGGER.exception(
                "maintenance job failed",
                extra={
                    "event": "maintenance_job_failed",
                    "job_name": job_name,
                    "worker_id": self._worker_id,
                },
            )
            with self._engine.begin() as connection:
                self._repository.update_job_checkpoint(
                    connection=connection,
                    job_name=job_name,
                    status="FAILED",
                    worker_id=self._worker_id,
                    run_id=run_id,
                    rows_processed=0,
                    next_scheduled_at=next_run,
                    error_class="internal",
                )
                self._append_event(
                    connection,
                    AuditEventType.MAINTENANCE_JOB_FAILED,
                    reason_code="internal",
                    safe_metadata={"job_name": job_name},
                )
            self._telemetry.record_maintenance(
                job_name=job_name,
                status="failed",
                rows_processed=0,
                duration_ms=duration_ms,
                failure_class="internal",
            )
            return MaintenanceResult(job_name, "FAILED", 0, duration_ms)

    def _append_event(
        self,
        connection: Connection,
        event_type: AuditEventType,
        *,
        reason_code: str,
        safe_metadata: dict[str, object],
    ) -> None:
        self._audit.append(
            connection,
            AuditEvent(
                event_type=event_type,
                actor_type="SYSTEM",
                outcome="RECORDED",
                correlation_id=f"maintenance-{uuid4()}",
                request_id=f"maintenance-{uuid4()}",
                reason_code=reason_code,
                safe_metadata={**safe_metadata, "local_only": True},
            ),
        )
