"""PostgreSQL audit outbox authority with short claim/finalize transactions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection, Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.audit.outbox_models import (
    AuditDeliveryMessage,
    AuditDestination,
    DeliveryFailureClass,
    DeliveryResult,
    DeliveryResultKind,
    OutboxBacklog,
    OutboxStatus,
    RetryPolicy,
    event_family,
)
from portal_api.db.metadata import (
    audit_archive_outbox,
    portal_maintenance_jobs,
    portal_provider_logout_receipts,
    portal_sessions,
    portal_token_envelopes,
)
from portal_api.db.unit_of_work import local_transaction

Clock = Callable[[], datetime]


class AuditOutboxRepository:
    """Own mutable delivery state while leaving the audit ledger append-only."""

    def __init__(
        self,
        engine: Engine,
        *,
        clock: Clock | None = None,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._engine = engine
        self._clock = clock or (lambda: datetime.now(UTC))
        self._audit = audit_ledger or AuditLedger()

    def claim_batch(
        self,
        *,
        worker_id: str,
        batch_size: int,
        lease_seconds: float,
        max_attempts: int | None = None,
    ) -> tuple[AuditDeliveryMessage, ...]:
        if max_attempts is not None and max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        now = self._clock()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        claims: list[AuditDeliveryMessage] = []
        with local_transaction(self._engine) as connection:
            rows = (
                connection.execute(
                    select(audit_archive_outbox)
                    .where(
                        audit_archive_outbox.c.publication_state.in_(
                            (
                                OutboxStatus.PENDING.value,
                                OutboxStatus.RETRY_SCHEDULED.value,
                            )
                        ),
                        audit_archive_outbox.c.next_attempt_at <= now,
                    )
                    .order_by(
                        audit_archive_outbox.c.next_attempt_at,
                        audit_archive_outbox.c.created_at,
                        audit_archive_outbox.c.outbox_id,
                    )
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .all()
            )
            for row in rows:
                lease_token = uuid4()
                attempt_count = int(row["attempt_count"]) + 1
                effective_max_attempts = (
                    int(row["max_attempts"]) if max_attempts is None else max_attempts
                )
                connection.execute(
                    update(audit_archive_outbox)
                    .where(
                        audit_archive_outbox.c.outbox_id == row["outbox_id"],
                        audit_archive_outbox.c.publication_state == row["publication_state"],
                    )
                    .values(
                        publication_state=OutboxStatus.LEASED.value,
                        attempt_count=attempt_count,
                        max_attempts=effective_max_attempts,
                        lease_owner=worker_id,
                        lease_token=lease_token,
                        lease_expires_at=lease_expires_at,
                        last_attempt_at=now,
                        updated_at=now,
                    )
                )
                destination = AuditDestination(str(row["destination"]))
                claims.append(
                    AuditDeliveryMessage(
                        outbox_id=row["outbox_id"],
                        audit_event_id=row["event_id"],
                        event_type=str(row["event_type"]),
                        event_family=event_family(str(row["event_type"])),
                        destination=destination,
                        payload_version=int(row["payload_version"]),
                        payload=dict(row["payload"]),
                        attempt_count=attempt_count,
                        max_attempts=effective_max_attempts,
                        lease_token=lease_token,
                    )
                )
        return tuple(claims)

    def finalize(
        self,
        message: AuditDeliveryMessage,
        result: DeliveryResult,
        *,
        retry_policy: RetryPolicy,
    ) -> bool:
        now = self._clock()
        with local_transaction(self._engine) as connection:
            current = (
                connection.execute(
                    select(audit_archive_outbox)
                    .where(audit_archive_outbox.c.outbox_id == message.outbox_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                current is None
                or current["publication_state"] != OutboxStatus.LEASED.value
                or current["lease_token"] != message.lease_token
            ):
                return False
            if result.kind in {
                DeliveryResultKind.SUCCESS,
                DeliveryResultKind.ALREADY_DELIVERED,
            }:
                connection.execute(
                    update(audit_archive_outbox)
                    .where(audit_archive_outbox.c.outbox_id == message.outbox_id)
                    .values(
                        publication_state=OutboxStatus.DELIVERED.value,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        delivered_at=now,
                        archive_reference=result.safe_reference,
                        archive_checksum=result.checksum,
                        last_error_class=None,
                        updated_at=now,
                    )
                )
                return True

            dead_letter = (
                result.kind is DeliveryResultKind.PERMANENT_FAILURE
                or message.attempt_count >= message.max_attempts
            )
            if dead_letter:
                connection.execute(
                    update(audit_archive_outbox)
                    .where(audit_archive_outbox.c.outbox_id == message.outbox_id)
                    .values(
                        publication_state=OutboxStatus.DEAD_LETTERED.value,
                        lease_owner=None,
                        lease_token=None,
                        lease_expires_at=None,
                        dead_lettered_at=now,
                        last_error_class=result.failure_class.value,
                        updated_at=now,
                    )
                )
                self._append_operational_event(
                    connection,
                    AuditEventType.AUDIT_OUTBOX_DEAD_LETTERED,
                    reason_code=result.failure_class.value,
                    safe_metadata={
                        "destination": message.destination.value,
                        "event_family": message.event_family,
                        "attempt_count": message.attempt_count,
                    },
                )
                return True

            available_at = now + retry_policy.delay(message.attempt_count)
            connection.execute(
                update(audit_archive_outbox)
                .where(audit_archive_outbox.c.outbox_id == message.outbox_id)
                .values(
                    publication_state=OutboxStatus.RETRY_SCHEDULED.value,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    next_attempt_at=available_at,
                    last_error_class=result.failure_class.value,
                    updated_at=now,
                )
            )
            self._append_operational_event(
                connection,
                AuditEventType.AUDIT_OUTBOX_DELIVERY_FAILED,
                reason_code=result.failure_class.value,
                safe_metadata={
                    "destination": message.destination.value,
                    "event_family": message.event_family,
                    "attempt_count": message.attempt_count,
                },
            )
            return True

    def recover_expired_leases(self, *, batch_size: int) -> int:
        now = self._clock()
        with local_transaction(self._engine) as connection:
            rows = (
                connection.execute(
                    select(
                        audit_archive_outbox.c.outbox_id,
                        audit_archive_outbox.c.destination,
                        audit_archive_outbox.c.event_type,
                    )
                    .where(
                        audit_archive_outbox.c.publication_state == OutboxStatus.LEASED.value,
                        audit_archive_outbox.c.lease_expires_at.is_not(None),
                        audit_archive_outbox.c.lease_expires_at <= now,
                    )
                    .order_by(audit_archive_outbox.c.lease_expires_at)
                    .limit(batch_size)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .all()
            )
            if not rows:
                return 0
            ids = [row["outbox_id"] for row in rows]
            connection.execute(
                update(audit_archive_outbox)
                .where(audit_archive_outbox.c.outbox_id.in_(ids))
                .values(
                    publication_state=OutboxStatus.RETRY_SCHEDULED.value,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                    next_attempt_at=now,
                    last_error_class=DeliveryFailureClass.DESTINATION_UNAVAILABLE.value,
                    updated_at=now,
                )
            )
            self._append_operational_event(
                connection,
                AuditEventType.AUDIT_OUTBOX_LEASE_RECOVERED,
                reason_code="EXPIRED_WORKER_LEASE",
                safe_metadata={"rows_processed": len(rows)},
            )
            return len(rows)

    def dead_letters(self, *, limit: int = 100) -> tuple[Mapping[str, Any], ...]:
        with self._engine.connect() as connection:
            rows = (
                connection.execute(
                    select(
                        audit_archive_outbox.c.outbox_id,
                        audit_archive_outbox.c.event_id,
                        audit_archive_outbox.c.event_type,
                        audit_archive_outbox.c.destination,
                        audit_archive_outbox.c.attempt_count,
                        audit_archive_outbox.c.last_error_class,
                        audit_archive_outbox.c.dead_lettered_at,
                        audit_archive_outbox.c.requeue_count,
                    )
                    .where(
                        audit_archive_outbox.c.publication_state == OutboxStatus.DEAD_LETTERED.value
                    )
                    .order_by(audit_archive_outbox.c.dead_lettered_at)
                    .limit(limit)
                )
                .mappings()
                .all()
            )
            return tuple(dict(row) for row in rows)

    def requeue(self, outbox_id: UUID) -> bool:
        now = self._clock()
        with local_transaction(self._engine) as connection:
            row = (
                connection.execute(
                    select(audit_archive_outbox)
                    .where(audit_archive_outbox.c.outbox_id == outbox_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                row is None
                or row["publication_state"] != OutboxStatus.DEAD_LETTERED.value
                or int(row["requeue_count"]) >= 10
            ):
                return False
            connection.execute(
                update(audit_archive_outbox)
                .where(audit_archive_outbox.c.outbox_id == outbox_id)
                .values(
                    publication_state=OutboxStatus.PENDING.value,
                    attempt_count=0,
                    next_attempt_at=now,
                    dead_lettered_at=None,
                    last_error_class=None,
                    requeue_count=int(row["requeue_count"]) + 1,
                    updated_at=now,
                )
            )
            self._append_operational_event(
                connection,
                AuditEventType.AUDIT_OUTBOX_REQUEUED,
                reason_code="OPERATOR_REQUEUE",
                safe_metadata={
                    "destination": str(row["destination"]),
                    "event_family": event_family(str(row["event_type"])),
                    "requeue_count": int(row["requeue_count"]) + 1,
                },
            )
            return True

    def backlog(self) -> OutboxBacklog:
        now = self._clock()
        with self._engine.connect() as connection:
            count_rows = connection.execute(
                select(
                    audit_archive_outbox.c.publication_state,
                    func.count().label("count"),
                )
                .where(
                    audit_archive_outbox.c.publication_state.in_(
                        (
                            OutboxStatus.PENDING.value,
                            OutboxStatus.LEASED.value,
                            OutboxStatus.RETRY_SCHEDULED.value,
                            OutboxStatus.DEAD_LETTERED.value,
                        )
                    )
                )
                .group_by(audit_archive_outbox.c.publication_state)
            ).all()
            counts: dict[str, int] = {
                str(row._mapping["publication_state"]): int(row._mapping["count"])
                for row in count_rows
            }
            oldest = connection.execute(
                select(func.min(audit_archive_outbox.c.created_at)).where(
                    audit_archive_outbox.c.publication_state.in_(
                        (
                            OutboxStatus.PENDING.value,
                            OutboxStatus.LEASED.value,
                            OutboxStatus.RETRY_SCHEDULED.value,
                        )
                    )
                )
            ).scalar_one()
        age = max(0.0, (now - oldest).total_seconds()) if oldest is not None else 0.0
        return OutboxBacklog(
            pending=int(counts.get(OutboxStatus.PENDING.value, 0)),
            leased=int(counts.get(OutboxStatus.LEASED.value, 0)),
            retry_scheduled=int(counts.get(OutboxStatus.RETRY_SCHEDULED.value, 0)),
            dead_lettered=int(counts.get(OutboxStatus.DEAD_LETTERED.value, 0)),
            oldest_pending_age_seconds=age,
            observed_at=now,
        )

    def cleanup_delivered(self, *, older_than: datetime, batch_size: int) -> int:
        return self._bounded_delete(
            audit_archive_outbox,
            audit_archive_outbox.c.outbox_id,
            and_(
                audit_archive_outbox.c.publication_state == OutboxStatus.DELIVERED.value,
                audit_archive_outbox.c.delivered_at < older_than,
            ),
            batch_size,
        )

    def cleanup_expired_replays(self, *, older_than: datetime, batch_size: int) -> int:
        return self._bounded_delete(
            portal_provider_logout_receipts,
            portal_provider_logout_receipts.c.receipt_id,
            portal_provider_logout_receipts.c.expires_at < older_than,
            batch_size,
        )

    def cleanup_terminal_envelopes(self, *, older_than: datetime, batch_size: int) -> int:
        active_family = select(portal_sessions.c.session_id).where(
            portal_sessions.c.session_family_id == portal_token_envelopes.c.session_family_id,
            portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
        )
        return self._bounded_delete(
            portal_token_envelopes,
            portal_token_envelopes.c.envelope_id,
            and_(
                portal_token_envelopes.c.lifecycle_state.in_(
                    ("EXPIRED", "LOGGED_OUT", "REVOKED", "DISPOSED")
                ),
                portal_token_envelopes.c.disposed_at.is_not(None),
                portal_token_envelopes.c.disposed_at < older_than,
                ~active_family.exists(),
            ),
            batch_size,
        )

    def update_job_checkpoint(
        self,
        *,
        connection: Connection,
        job_name: str,
        status: str,
        worker_id: str,
        run_id: UUID,
        rows_processed: int,
        next_scheduled_at: datetime,
        error_class: str | None = None,
    ) -> None:
        now = self._clock()
        values = {
            "job_name": job_name,
            "status": status,
            "last_started_at": now if status == "RUNNING" else None,
            "last_completed_at": now if status == "SUCCEEDED" else None,
            "last_failed_at": now if status == "FAILED" else None,
            "last_successful_run_id": run_id if status == "SUCCEEDED" else None,
            "lease_owner": worker_id if status == "RUNNING" else None,
            "lease_token": run_id if status == "RUNNING" else None,
            "lease_expires_at": next_scheduled_at if status == "RUNNING" else None,
            "last_rows_processed": rows_processed,
            "next_scheduled_at": next_scheduled_at,
            "updated_at": now,
        }
        update_values: dict[str, Any] = {
            "status": status,
            "lease_owner": worker_id if status == "RUNNING" else None,
            "lease_token": run_id if status == "RUNNING" else None,
            "lease_expires_at": next_scheduled_at if status == "RUNNING" else None,
            "last_rows_processed": rows_processed,
            "last_error_class": error_class,
            "next_scheduled_at": next_scheduled_at,
            "updated_at": now,
        }
        if status == "RUNNING":
            update_values["last_started_at"] = now
        elif status == "SUCCEEDED":
            update_values["last_completed_at"] = now
            update_values["last_successful_run_id"] = run_id
            update_values["last_error_class"] = None
        elif status == "FAILED":
            update_values["last_failed_at"] = now
            update_values["last_error_class"] = error_class
        statement = pg_insert(portal_maintenance_jobs).values(**values)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[portal_maintenance_jobs.c.job_name],
                set_=update_values,
            )
        )

    def _bounded_delete(
        self,
        table: Any,
        id_column: Any,
        predicate: Any,
        batch_size: int,
    ) -> int:
        with local_transaction(self._engine) as connection:
            ids = connection.execute(
                select(id_column).where(predicate).order_by(id_column).limit(batch_size)
            ).scalars()
            selected = tuple(ids)
            if not selected:
                return 0
            result = connection.execute(delete(table).where(id_column.in_(selected)))
            return int(result.rowcount or 0)

    def _append_operational_event(
        self,
        connection: Connection,
        event_type: AuditEventType,
        *,
        reason_code: str,
        safe_metadata: dict[str, Any],
    ) -> None:
        self._audit.append(
            connection,
            AuditEvent(
                event_type=event_type,
                actor_type="SYSTEM",
                outcome="RECORDED",
                correlation_id=f"audit-worker-{uuid4()}",
                request_id=f"audit-worker-{uuid4()}",
                reason_code=reason_code,
                safe_metadata={**safe_metadata, "local_only": True},
            ),
        )
