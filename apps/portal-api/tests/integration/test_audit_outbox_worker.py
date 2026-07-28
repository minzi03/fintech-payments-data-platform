"""PostgreSQL outbox, fencing, idempotency, and maintenance integration tests."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from portal_api.audit.delivery import PostgresReceiptDestination
from portal_api.audit.ledger import AuditLedger
from portal_api.audit.maintenance import MaintenanceCoordinator
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.audit.outbox import AuditOutboxRepository
from portal_api.audit.outbox_models import (
    DeliveryFailureClass,
    DeliveryResult,
    DeliveryResultKind,
    OutboxStatus,
    RetryPolicy,
)
from portal_api.core.config import PortalApiSettings
from portal_api.db.unit_of_work import local_transaction
from portal_api.telemetry.metrics import InMemoryTelemetry
from sqlalchemy import create_engine, text


def _url(name: str) -> str:
    value = os.environ.get(name, "")
    if value:
        return value
    migration = os.environ.get("PORTAL_TEST_MIGRATION_DATABASE_URL", "")
    if name == "PORTAL_TEST_ARCHIVE_DATABASE_URL" and migration:
        return migration.replace("portal_migration:", "portal_archive:")
    pytest.skip(f"{name} is not configured")


def _event(*, local_only: bool = False) -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.LOGIN_STARTED,
        actor_type="ANONYMOUS",
        outcome="STARTED",
        correlation_id=f"correlation-{uuid4()}",
        request_id=f"request-{uuid4()}",
        subject_reference="raw-subject-must-not-enter-outbox",
        safe_metadata={"safe_reference": str(uuid4()), "local_only": local_only},
    )


def _append(event: AuditEvent) -> None:
    engine = create_engine(_url("PORTAL_TEST_RUNTIME_DATABASE_URL"))
    try:
        with local_transaction(engine) as connection:
            AuditLedger().append(connection, event)
    finally:
        engine.dispose()


def _prioritize(*event_ids: object) -> None:
    engine = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE portal_control.audit_archive_outbox "
                    "SET next_attempt_at = CASE WHEN event_id = ANY(:event_ids) "
                    "THEN CURRENT_TIMESTAMP - interval '1 hour' "
                    "ELSE CURRENT_TIMESTAMP + interval '1 hour' END "
                    "WHERE publication_state IN ('PENDING', 'RETRY_SCHEDULED')"
                ),
                {"event_ids": list(event_ids)},
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_transactional_payload_is_versioned_privacy_safe_and_local_only_skips_outbox() -> None:
    normal = _event()
    local_only = _event(local_only=True)
    _append(normal)
    _append(local_only)
    engine = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    try:
        with engine.connect() as connection:
            payload = connection.execute(
                text(
                    "SELECT payload FROM portal_control.audit_archive_outbox "
                    "WHERE event_id = :event_id"
                ),
                {"event_id": normal.event_id},
            ).scalar_one()
            assert payload["schema_version"] == 1
            assert payload["event_id"] == str(normal.event_id)
            assert "raw-subject-must-not-enter-outbox" not in str(payload)
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.audit_archive_outbox "
                        "WHERE event_id = :event_id"
                    ),
                    {"event_id": local_only.event_id},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_ledger_and_outbox_roll_back_atomically() -> None:
    event = _event()
    engine = create_engine(_url("PORTAL_TEST_RUNTIME_DATABASE_URL"))
    try:
        with (
            pytest.raises(RuntimeError, match="force rollback"),
            local_transaction(engine) as connection,
        ):
            AuditLedger().append(connection, event)
            raise RuntimeError("force rollback")
    finally:
        engine.dispose()
    verification = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    try:
        with verification.connect() as connection:
            ledger_count = connection.execute(
                text(
                    "SELECT count(*) FROM portal_control.security_audit_events "
                    "WHERE event_id = :event_id"
                ),
                {"event_id": event.event_id},
            ).scalar_one()
            outbox_count = connection.execute(
                text(
                    "SELECT count(*) FROM portal_control.audit_archive_outbox "
                    "WHERE event_id = :event_id"
                ),
                {"event_id": event.event_id},
            ).scalar_one()
        assert ledger_count == outbox_count == 0
    finally:
        verification.dispose()


@pytest.mark.integration
def test_claim_delivery_idempotency_and_stale_fencing() -> None:
    event = _event()
    _append(event)
    _prioritize(event.event_id)
    engine = create_engine(_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
    repository = AuditOutboxRepository(engine)
    destination = PostgresReceiptDestination(engine, timeout_seconds=2)
    try:
        claims = repository.claim_batch(worker_id="worker-a", batch_size=10, lease_seconds=30)
        message = next(item for item in claims if item.audit_event_id == event.event_id)
        first = asyncio.run(destination.deliver(message))
        duplicate = asyncio.run(destination.deliver(message))
        assert first.kind is DeliveryResultKind.SUCCESS
        assert duplicate.kind is DeliveryResultKind.ALREADY_DELIVERED
        assert repository.finalize(
            message,
            duplicate,
            retry_policy=RetryPolicy(1, 10),
        )
        assert not repository.finalize(
            message,
            first,
            retry_policy=RetryPolicy(1, 10),
        )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_competing_workers_skip_locked_and_expired_lease_recovers() -> None:
    first_event = _event()
    second_event = _event()
    _append(first_event)
    _append(second_event)
    _prioritize(first_event.event_id, second_event.event_id)
    engine = create_engine(_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
    repository = AuditOutboxRepository(engine)
    try:
        first = repository.claim_batch(worker_id="worker-a", batch_size=1, lease_seconds=30)
        second = repository.claim_batch(worker_id="worker-b", batch_size=1, lease_seconds=30)
        assert len(first) == len(second) == 1
        assert first[0].outbox_id != second[0].outbox_id
        migration = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
        try:
            with migration.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE portal_control.audit_archive_outbox "
                        "SET lease_expires_at = CURRENT_TIMESTAMP - interval '1 second' "
                        "WHERE outbox_id = :outbox_id"
                    ),
                    {"outbox_id": first[0].outbox_id},
                )
        finally:
            migration.dispose()
        assert repository.recover_expired_leases(batch_size=10) >= 1
        assert not repository.finalize(
            first[0],
            DeliveryResult(DeliveryResultKind.SUCCESS),
            retry_policy=RetryPolicy(1, 10),
        )
    finally:
        engine.dispose()


@pytest.mark.integration
def test_permanent_failure_dead_letters_and_operator_requeue_is_non_recursive() -> None:
    event = _event()
    _append(event)
    migration = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    try:
        with migration.begin() as connection:
            connection.execute(
                text(
                    "UPDATE portal_control.audit_archive_outbox "
                    "SET max_attempts = 1, next_attempt_at = CURRENT_TIMESTAMP - interval '1 hour' "
                    "WHERE event_id = :event_id"
                ),
                {"event_id": event.event_id},
            )
    finally:
        migration.dispose()
    engine = create_engine(_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
    repository = AuditOutboxRepository(engine)
    try:
        message = next(
            item
            for item in repository.claim_batch(
                worker_id="worker-dead", batch_size=10, lease_seconds=30
            )
            if item.audit_event_id == event.event_id
        )
        assert repository.finalize(
            message,
            DeliveryResult(
                DeliveryResultKind.PERMANENT_FAILURE,
                DeliveryFailureClass.PERMANENT_REJECTION,
            ),
            retry_policy=RetryPolicy(1, 10),
        )
        assert repository.requeue(message.outbox_id)
        verification = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
        try:
            with verification.connect() as connection:
                state = connection.execute(
                    text(
                        "SELECT publication_state, requeue_count "
                        "FROM portal_control.audit_archive_outbox "
                        "WHERE outbox_id = :outbox_id"
                    ),
                    {"outbox_id": message.outbox_id},
                ).one()
                recursive = connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.audit_archive_outbox "
                        "WHERE event_type LIKE 'operations.audit_outbox_%'"
                    )
                ).scalar_one()
            assert state == (OutboxStatus.PENDING.value, 1)
            assert recursive == 0
        finally:
            verification.dispose()
    finally:
        engine.dispose()


@pytest.mark.integration
def test_replay_cleanup_respects_retention_boundary_and_maintenance_lock() -> None:
    migration = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    expired = uuid4()
    active = uuid4()
    now = datetime.now(UTC)
    try:
        with migration.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO portal_control.portal_provider_logout_receipts "
                    "(receipt_id, provider_id, jti_hash, issued_at, expires_at) "
                    "VALUES (:expired, 'test', :expired_hash, :issued, :expired_at), "
                    "(:active, 'test', :active_hash, :issued, :active_at)"
                ),
                {
                    "expired": expired,
                    "active": active,
                    "expired_hash": b"expired-" + expired.bytes,
                    "active_hash": b"active-" + active.bytes,
                    "issued": now - timedelta(hours=1),
                    "expired_at": now - timedelta(minutes=10),
                    "active_at": now + timedelta(minutes=10),
                },
            )
        archive = create_engine(_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
        repository = AuditOutboxRepository(archive, clock=lambda: now)
        telemetry = InMemoryTelemetry()
        settings = PortalApiSettings(
            _env_file=None,
            audit_outbox_enabled=True,
            audit_worker_database_url=_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"),
            replay_retention_buffer_seconds=300,
        )
        coordinator = MaintenanceCoordinator(
            engine=archive,
            repository=repository,
            settings=settings,
            telemetry=telemetry,
            worker_id="maintenance-test",
            clock=lambda: now,
        )
        try:
            with migration.connect() as lock_connection:
                lock_connection.execute(text("SELECT pg_advisory_lock(7162005001)"))
                lock_connection.commit()
                results = coordinator.run_all()
                lock_connection.execute(text("SELECT pg_advisory_unlock(7162005001)"))
                lock_connection.commit()
            assert results[0].status == "OVERLAP_SKIPPED"
            with migration.connect() as connection:
                remaining = set(
                    connection.execute(
                        text(
                            "SELECT receipt_id FROM "
                            "portal_control.portal_provider_logout_receipts "
                            "WHERE receipt_id IN (:expired, :active)"
                        ),
                        {"expired": expired, "active": active},
                    ).scalars()
                )
            assert remaining == {active}
        finally:
            archive.dispose()
    finally:
        with migration.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM portal_control.portal_provider_logout_receipts "
                    "WHERE receipt_id IN (:expired, :active)"
                ),
                {"expired": expired, "active": active},
            )
        migration.dispose()


@pytest.mark.integration
def test_delivered_outbox_cleanup_is_bounded_and_preserves_ledger() -> None:
    old_event = _event()
    recent_event = _event()
    _append(old_event)
    _append(recent_event)
    now = datetime.now(UTC)
    migration = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    archive = create_engine(_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
    try:
        with migration.begin() as connection:
            connection.execute(
                text(
                    "UPDATE portal_control.audit_archive_outbox "
                    "SET publication_state = 'DELIVERED', delivered_at = CASE "
                    "WHEN event_id = :old_event THEN :old_time ELSE :recent_time END "
                    "WHERE event_id IN (:old_event, :recent_event)"
                ),
                {
                    "old_event": old_event.event_id,
                    "recent_event": recent_event.event_id,
                    "old_time": now - timedelta(days=10),
                    "recent_time": now - timedelta(days=1),
                },
            )
        repository = AuditOutboxRepository(archive, clock=lambda: now)
        assert (
            repository.cleanup_delivered(
                older_than=now - timedelta(days=7),
                batch_size=1,
            )
            == 1
        )
        with migration.connect() as connection:
            remaining_outbox = set(
                connection.execute(
                    text(
                        "SELECT event_id FROM portal_control.audit_archive_outbox "
                        "WHERE event_id IN (:old_event, :recent_event)"
                    ),
                    {
                        "old_event": old_event.event_id,
                        "recent_event": recent_event.event_id,
                    },
                ).scalars()
            )
            ledger_count = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.security_audit_events "
                        "WHERE event_id IN (:old_event, :recent_event)"
                    ),
                    {
                        "old_event": old_event.event_id,
                        "recent_event": recent_event.event_id,
                    },
                ).scalar_one()
            )
        assert remaining_outbox == {recent_event.event_id}
        assert ledger_count == 2
    finally:
        archive.dispose()
        migration.dispose()


@pytest.mark.integration
def test_terminal_envelope_cleanup_never_deletes_active_session_authority() -> None:
    migration = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    archive = create_engine(_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
    principal_ids = (uuid4(), uuid4())
    session_ids = (uuid4(), uuid4())
    family_ids = (uuid4(), uuid4())
    envelope_ids = (uuid4(), uuid4())
    now = datetime.now(UTC)
    session_values = {
        "issuer": "https://identity.test/realms/portal",
        "tenant_id": "test-tenant",
        "authenticated_at": now - timedelta(days=10),
        "last_activity_at": now - timedelta(days=10),
        "idle_expires_at": now + timedelta(hours=1),
        "absolute_expires_at": now + timedelta(hours=2),
        "identity_verified_until": now + timedelta(minutes=30),
    }
    try:
        with migration.begin() as connection:
            for index in range(2):
                connection.execute(
                    text(
                        "INSERT INTO portal_control.portal_principals "
                        "(principal_id, issuer, subject_reference, status) "
                        "VALUES (:principal_id, :issuer, :subject, 'ACTIVE')"
                    ),
                    {
                        "principal_id": principal_ids[index],
                        "issuer": session_values["issuer"],
                        "subject": f"maintenance-subject-{principal_ids[index]}",
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO portal_control.portal_sessions ("
                        "session_id, session_lookup_hash, lookup_key_version, session_family_id, "
                        "principal_id, issuer, subject_reference, tenant_id, status, "
                        "security_epoch, roles_snapshot, environments_snapshot, mapping_revision, "
                        "policy_revision, capability_revision, authentication_assurance, "
                        "authenticated_at, last_activity_at, idle_expires_at, absolute_expires_at, "
                        "identity_verified_until, audit_correlation_id, csrf_token_hash, "
                        "csrf_generation"
                        ") VALUES ("
                        ":session_id, :lookup, 'test-v1', :family_id, :principal_id, :issuer, "
                        ":subject, :tenant_id, :status, 1, '[]'::jsonb, '[]'::jsonb, "
                        "'mapping-v1', 'policy-v1', 'capability-v1', 'loa1', :authenticated_at, "
                        ":last_activity_at, :idle_expires_at, :absolute_expires_at, "
                        ":identity_verified_until, :correlation, :csrf, 1)"
                    ),
                    {
                        **session_values,
                        "session_id": session_ids[index],
                        "lookup": b"lookup-" + session_ids[index].bytes,
                        "family_id": family_ids[index],
                        "principal_id": principal_ids[index],
                        "subject": f"maintenance-subject-{principal_ids[index]}",
                        "status": "ACTIVE" if index == 0 else "TERMINATED",
                        "correlation": f"maintenance-{session_ids[index]}",
                        "csrf": b"csrf-" + session_ids[index].bytes,
                    },
                )
                connection.execute(
                    text(
                        "INSERT INTO portal_control.portal_token_envelopes ("
                        "envelope_id, session_family_id, provider_id, provider_subject, "
                        "lifecycle_state, ciphertext, nonce, authentication_tag, wrapped_data_key, "
                        "wrapped_data_key_nonce, kms_key_id, token_generation, issued_at, "
                        "disposed_at"
                        ") VALUES ("
                        ":envelope_id, :family_id, 'test-provider', :subject, 'DISPOSED', "
                        ":ciphertext, :nonce, :tag, :wrapped_key, :wrapped_nonce, 'test-key', 1, "
                        ":issued_at, :disposed_at)"
                    ),
                    {
                        "envelope_id": envelope_ids[index],
                        "family_id": family_ids[index],
                        "subject": f"maintenance-subject-{principal_ids[index]}",
                        "ciphertext": b"ciphertext",
                        "nonce": b"n" * 12,
                        "tag": b"t" * 16,
                        "wrapped_key": b"wrapped",
                        "wrapped_nonce": b"w" * 12,
                        "issued_at": now - timedelta(days=10),
                        "disposed_at": now - timedelta(days=9),
                    },
                )

        repository = AuditOutboxRepository(archive, clock=lambda: now)
        assert (
            repository.cleanup_terminal_envelopes(
                older_than=now - timedelta(days=7),
                batch_size=10,
            )
            == 1
        )
        with migration.connect() as connection:
            remaining = set(
                connection.execute(
                    text(
                        "SELECT envelope_id FROM portal_control.portal_token_envelopes "
                        "WHERE envelope_id = ANY(:envelope_ids)"
                    ),
                    {"envelope_ids": list(envelope_ids)},
                ).scalars()
            )
        assert remaining == {envelope_ids[0]}
    finally:
        with migration.begin() as connection:
            connection.execute(
                text(
                    "DELETE FROM portal_control.portal_token_envelopes "
                    "WHERE envelope_id = ANY(:envelope_ids)"
                ),
                {"envelope_ids": list(envelope_ids)},
            )
            connection.execute(
                text(
                    "DELETE FROM portal_control.portal_sessions "
                    "WHERE session_id = ANY(:session_ids)"
                ),
                {"session_ids": list(session_ids)},
            )
            connection.execute(
                text(
                    "DELETE FROM portal_control.portal_principals "
                    "WHERE principal_id = ANY(:principal_ids)"
                ),
                {"principal_ids": list(principal_ids)},
            )
        archive.dispose()
        migration.dispose()


@pytest.mark.integration
def test_maintenance_checkpoint_preserves_failure_history_and_records_recovery() -> None:
    archive = create_engine(_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"))
    migration = create_engine(_url("PORTAL_TEST_MIGRATION_DATABASE_URL"))
    now = datetime.now(UTC)
    repository = AuditOutboxRepository(archive, clock=lambda: now)
    settings = PortalApiSettings(
        _env_file=None,
        audit_outbox_enabled=True,
        audit_worker_database_url=_url("PORTAL_TEST_ARCHIVE_DATABASE_URL"),
    )
    telemetry = InMemoryTelemetry()
    coordinator = MaintenanceCoordinator(
        engine=archive,
        repository=repository,
        settings=settings,
        telemetry=telemetry,
        worker_id="maintenance-recovery-test",
        clock=lambda: now,
    )
    try:
        with archive.begin() as connection:
            repository.update_job_checkpoint(
                connection=connection,
                job_name="recover_outbox_leases",
                status="FAILED",
                worker_id="failed-worker",
                run_id=uuid4(),
                rows_processed=0,
                next_scheduled_at=now,
                error_class="database",
            )
        with migration.connect() as connection:
            before = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.security_audit_events "
                        "WHERE event_type = 'operations.maintenance_job_recovered.v1'"
                    )
                ).scalar_one()
            )

        result = coordinator.run_all()[0]
        assert result.status == "SUCCEEDED"

        with migration.connect() as connection:
            checkpoint = connection.execute(
                text(
                    "SELECT status, last_failed_at, last_completed_at, last_error_class "
                    "FROM portal_control.portal_maintenance_jobs "
                    "WHERE job_name = 'recover_outbox_leases'"
                )
            ).one()
            after = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.security_audit_events "
                        "WHERE event_type = 'operations.maintenance_job_recovered.v1'"
                    )
                ).scalar_one()
            )
            recursive = int(
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.audit_archive_outbox "
                        "WHERE event_type = 'operations.maintenance_job_recovered.v1'"
                    )
                ).scalar_one()
            )
        assert checkpoint.status == "SUCCEEDED"
        assert checkpoint.last_failed_at is not None
        assert checkpoint.last_completed_at is not None
        assert checkpoint.last_error_class is None
        assert after == before + 1
        assert recursive == 0
    finally:
        archive.dispose()
        migration.dispose()
