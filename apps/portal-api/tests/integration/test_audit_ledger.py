"""Transactional append-only security audit integration tests."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest
from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.db.unit_of_work import local_transaction
from sqlalchemy import create_engine, text
from sqlalchemy.exc import ProgrammingError


def _runtime_engine():
    url = os.environ.get("PORTAL_TEST_RUNTIME_DATABASE_URL", "")
    if not url:
        pytest.skip("PORTAL_TEST_RUNTIME_DATABASE_URL is not configured")
    return create_engine(url)


def _verification_engine():
    url = os.environ.get("PORTAL_TEST_MIGRATION_DATABASE_URL", "")
    if not url:
        pytest.skip("PORTAL_TEST_MIGRATION_DATABASE_URL is not configured")
    return create_engine(url)


def _event() -> AuditEvent:
    return AuditEvent(
        event_type=AuditEventType.LOGIN_STARTED,
        actor_type="ANONYMOUS",
        outcome="STARTED",
        correlation_id=f"correlation-{uuid4()}",
        request_id=f"request-{uuid4()}",
        safe_metadata={"transaction_reference": str(uuid4())},
    )


@pytest.mark.integration
def test_audit_and_outbox_are_written_in_caller_transaction() -> None:
    engine = _runtime_engine()
    verification_engine = _verification_engine()
    event = _event()
    try:
        with local_transaction(engine) as connection:
            sequence = AuditLedger().append(connection, event)
            assert sequence > 0
        with verification_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.security_audit_events "
                        "WHERE event_id = :event_id"
                    ),
                    {"event_id": event.event_id},
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.audit_archive_outbox "
                        "WHERE event_id = :event_id"
                    ),
                    {"event_id": event.event_id},
                ).scalar_one()
                == 1
            )
    finally:
        engine.dispose()
        verification_engine.dispose()


@pytest.mark.integration
def test_caller_rollback_removes_audit_and_outbox_together() -> None:
    engine = _runtime_engine()
    verification_engine = _verification_engine()
    event = _event()
    try:
        with (
            pytest.raises(RuntimeError, match="force rollback"),
            local_transaction(engine) as connection,
        ):
            AuditLedger().append(connection, event)
            raise RuntimeError("force rollback")
        with verification_engine.connect() as connection:
            assert (
                connection.execute(
                    text(
                        "SELECT count(*) FROM portal_control.security_audit_events "
                        "WHERE event_id = :event_id"
                    ),
                    {"event_id": event.event_id},
                ).scalar_one()
                == 0
            )
    finally:
        engine.dispose()
        verification_engine.dispose()


@pytest.mark.integration
def test_runtime_cannot_mutate_audit_events() -> None:
    engine = _runtime_engine()
    try:
        with engine.begin() as connection, pytest.raises(ProgrammingError):
            connection.execute(text("UPDATE portal_control.security_audit_events SET outcome='x'"))
    finally:
        engine.dispose()
