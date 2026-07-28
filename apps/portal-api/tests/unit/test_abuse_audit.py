"""Selective abuse audit remains bounded and privacy safe."""

from __future__ import annotations

from portal_api.abuse.audit import AbuseAuditSink
from portal_api.abuse.models import (
    AbuseBackendStatus,
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationResult,
    AbuseOperation,
    PenaltyLevel,
)
from portal_api.audit.models import AuditEvent, AuditEventType
from sqlalchemy import create_engine


class CapturingLedger:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def append(self, connection: object, event: AuditEvent) -> int:
        del connection
        self.events.append(event)
        return len(self.events)


def _result(
    *,
    decision: AbuseDecision,
    penalty: PenaltyLevel = PenaltyLevel.NORMAL,
) -> AbuseEvaluationResult:
    return AbuseEvaluationResult(
        decision=decision,
        policy_name="login-v1",
        policy_version="development-v1",
        limiting_dimension=AbuseDimension.IP_PREFIX,
        retry_after_seconds=30,
        remaining=0,
        backend_status=AbuseBackendStatus.UP,
        penalty_level=penalty,
    )


def test_allowed_requests_are_not_audited() -> None:
    sink = AbuseAuditSink(create_engine("sqlite+pysqlite:///:memory:"))
    ledger = CapturingLedger()
    sink._ledger = ledger  # type: ignore[assignment]

    sink.record(
        operation=AbuseOperation.LOGIN_INITIATION,
        result=_result(decision=AbuseDecision.ALLOW),
        correlation_id="correlation",
        request_id="request",
    )

    assert ledger.events == []


def test_provider_throttle_audit_has_only_bounded_classifications() -> None:
    sink = AbuseAuditSink(create_engine("sqlite+pysqlite:///:memory:"))
    ledger = CapturingLedger()
    sink._ledger = ledger  # type: ignore[assignment]

    sink.record(
        operation=AbuseOperation.BACKGROUND_REFRESH,
        result=_result(decision=AbuseDecision.THROTTLE),
        correlation_id="correlation",
        request_id="request",
    )

    event = ledger.events[0]
    assert event.event_type is AuditEventType.ABUSE_PROVIDER_OPERATION_THROTTLED
    assert event.safe_metadata == {
        "operation": "background_refresh",
        "policy": "login-v1",
        "dimension": "ip_prefix",
        "retry_after_seconds": 30,
        "penalty_level": "normal",
        "backend_status": "up",
    }
    serialized = repr(event)
    assert "192.0.2" not in serialized
    assert "access_token" not in serialized


def test_backend_outage_and_extended_penalty_have_distinct_events() -> None:
    sink = AbuseAuditSink(create_engine("sqlite+pysqlite:///:memory:"))
    ledger = CapturingLedger()
    sink._ledger = ledger  # type: ignore[assignment]
    degraded = AbuseEvaluationResult(
        decision=AbuseDecision.DEGRADED_ALLOW,
        policy_name="login-v1",
        policy_version="development-v1",
        limiting_dimension=None,
        retry_after_seconds=0,
        remaining=0,
        backend_status=AbuseBackendStatus.UNAVAILABLE,
        penalty_level=PenaltyLevel.NORMAL,
    )

    sink.record(
        operation=AbuseOperation.LOGIN_INITIATION,
        result=degraded,
        correlation_id="correlation",
        request_id="request",
        reason_code="ABUSE_BACKEND_UNAVAILABLE",
    )
    sink.record(
        operation=AbuseOperation.LOGIN_INITIATION,
        result=_result(
            decision=AbuseDecision.TEMPORARILY_BLOCK,
            penalty=PenaltyLevel.EXTENDED_BLOCK,
        ),
        correlation_id="correlation",
        request_id="request",
    )

    assert [event.event_type for event in ledger.events] == [
        AuditEventType.ABUSE_BACKEND_UNAVAILABLE,
        AuditEventType.ABUSE_PENALTY_ESCALATED,
    ]
