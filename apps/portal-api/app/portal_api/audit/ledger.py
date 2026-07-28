"""Append security evidence and archive intent inside a caller-owned transaction."""

from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection

from portal_api.audit.models import AuditEvent
from portal_api.audit.redaction import assert_audit_safe
from portal_api.telemetry.database import telemetry_for_engine


class AuditLedger:
    """Invoke the migration-owned append function without committing independently."""

    def append(self, connection: Connection, event: AuditEvent) -> int:
        assert_audit_safe(event.safe_metadata)
        telemetry = telemetry_for_engine(connection.engine)
        payload = {
            field: self._json_value(getattr(event, field))
            for field in (
                "event_id",
                "deduplication_key",
                "occurred_at",
                "actor_type",
                "principal_id",
                "issuer_id",
                "subject_reference",
                "session_reference",
                "tenant_id",
                "environment_id",
                "action",
                "resource_type",
                "resource_reference",
                "capability_id",
                "decision",
                "reason_code",
                "policy_revision",
                "capability_revision",
                "authentication_assurance",
                "correlation_id",
                "request_id",
                "source_network_classification",
                "user_agent_classification",
                "outcome",
                "safe_metadata",
            )
        }
        payload["event_type"] = event.event_type.value
        span = (
            telemetry.span(
                "portal.audit.persist",
                attributes={
                    "audit.event.type": event.event_type.value,
                    "audit.outcome": event.outcome,
                },
            )
            if telemetry is not None
            else nullcontext()
        )
        with span:
            ledger_sequence = connection.execute(
                text(
                    "SELECT portal_control.append_security_audit_event(:event_payload)"
                ).bindparams(bindparam("event_payload", type_=JSONB)),
                {"event_payload": payload},
            ).scalar_one()
            if telemetry is not None:
                telemetry.record_audit_event(event.event_type.value, event.outcome)
        return int(ledger_sequence)

    @staticmethod
    def _json_value(value: object) -> object:
        if isinstance(value, UUID):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        return value
