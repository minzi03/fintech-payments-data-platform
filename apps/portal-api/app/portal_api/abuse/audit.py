"""Selective append-only audit sink for security-significant abuse outcomes."""

from __future__ import annotations

from sqlalchemy.engine import Engine

from portal_api.abuse.models import (
    AbuseDecision,
    AbuseDimension,
    AbuseEvaluationResult,
    AbuseOperation,
)
from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.db.unit_of_work import local_transaction


class AbuseAuditSink:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._ledger = AuditLedger()

    def record(
        self,
        *,
        operation: AbuseOperation,
        result: AbuseEvaluationResult,
        correlation_id: str,
        request_id: str,
        reason_code: str | None = None,
    ) -> None:
        event_type = self._event_type(
            operation,
            result,
            reason_code=reason_code,
        )
        if event_type is None:
            return
        with local_transaction(self._engine) as connection:
            self._ledger.append(
                connection,
                AuditEvent(
                    event_type=event_type,
                    actor_type="system",
                    outcome=result.decision.value.upper(),
                    correlation_id=correlation_id,
                    request_id=request_id,
                    action=f"portal.abuse.{operation.value}",
                    decision=result.decision.value,
                    reason_code=reason_code,
                    policy_revision=result.policy_version,
                    source_network_classification=(
                        "privacy_safe_prefix"
                        if result.limiting_dimension is AbuseDimension.IP_PREFIX
                        else None
                    ),
                    safe_metadata={
                        "operation": operation.value,
                        "policy": result.policy_name,
                        "dimension": (
                            result.limiting_dimension.value
                            if result.limiting_dimension is not None
                            else None
                        ),
                        "retry_after_seconds": result.retry_after_seconds,
                        "penalty_level": result.penalty_level.value,
                        "backend_status": result.backend_status.value,
                    },
                ),
            )

    @staticmethod
    def _event_type(
        operation: AbuseOperation,
        result: AbuseEvaluationResult,
        *,
        reason_code: str | None,
    ) -> AuditEventType | None:
        if reason_code == "ABUSE_BACKEND_UNAVAILABLE":
            return AuditEventType.ABUSE_BACKEND_UNAVAILABLE
        if operation in {
            AbuseOperation.BACKGROUND_REFRESH,
            AbuseOperation.PROVIDER_END_SESSION,
            AbuseOperation.ACCESS_TOKEN_REVOCATION,
            AbuseOperation.REFRESH_TOKEN_REVOCATION,
        } and result.decision in {
            AbuseDecision.THROTTLE,
            AbuseDecision.TEMPORARILY_BLOCK,
            AbuseDecision.DEGRADED_DENY,
        }:
            return AuditEventType.ABUSE_PROVIDER_OPERATION_THROTTLED
        if result.penalty_level.value == "extended_block":
            return AuditEventType.ABUSE_PENALTY_ESCALATED
        if result.decision is AbuseDecision.TEMPORARILY_BLOCK:
            return AuditEventType.ABUSE_TEMPORARY_BLOCK_APPLIED
        if result.decision is AbuseDecision.THROTTLE:
            return AuditEventType.ABUSE_REQUEST_THROTTLED
        if result.decision in {AbuseDecision.DEGRADED_ALLOW, AbuseDecision.DEGRADED_DENY}:
            return AuditEventType.ABUSE_FALLBACK_ACTIVATED
        return None
