"""Frozen Portal security audit event model."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4


class AuditEventType(StrEnum):
    LOGIN_STARTED = "auth.login_started.v1"
    LOGIN_SUCCEEDED = "auth.login_succeeded.v1"
    LOGIN_FAILED = "auth.login_failed.v1"
    SESSION_CREATED = "auth.session_created.v1"
    SESSION_ROTATED = "auth.session_rotated.v1"
    SESSION_REFRESHED = "auth.session_refreshed.v1"
    PROVIDER_REFRESH_STARTED = "auth.provider_refresh_started.v1"
    PROVIDER_REFRESH_SUCCEEDED = "auth.provider_refresh_succeeded.v1"
    PROVIDER_REFRESH_FAILED = "auth.provider_refresh_failed.v1"
    PROVIDER_REFRESH_REUSE_DETECTED = "auth.provider_refresh_reuse_detected.v1"
    PROVIDER_SESSION_STATE_CHANGED = "auth.provider_session_state_changed.v1"
    PROVIDER_REVOCATION_SUCCEEDED = "auth.provider_revocation_succeeded.v1"
    PROVIDER_REVOCATION_FAILED = "auth.provider_revocation_failed.v1"
    PROVIDER_LOGOUT_SUCCEEDED = "auth.provider_logout_succeeded.v1"
    SESSION_EXPIRED_IDLE = "auth.session_expired_idle.v1"
    SESSION_EXPIRED_ABSOLUTE = "auth.session_expired_absolute.v1"
    SESSION_REVOKED = "auth.session_revoked.v1"
    LOGOUT_REQUESTED = "auth.logout_requested.v1"
    LOGOUT_COMPLETED = "auth.logout_completed.v1"
    PROVIDER_LOGOUT_FAILED = "auth.provider_logout_failed.v1"
    PROVIDER_BACKCHANNEL_LOGOUT = "auth.provider_backchannel_logout.v1"
    PROVIDER_TOKEN_DISPOSED = "auth.provider_token_disposed.v1"
    CSRF_REJECTED = "auth.csrf_rejected.v1"
    AUTHORIZATION_ALLOWED = "authz.decision_allowed.v1"
    AUTHORIZATION_DENIED = "authz.decision_denied.v1"
    AUTHORIZATION_INDETERMINATE = "authz.decision_indeterminate.v1"
    ENVIRONMENT_SELECTED = "authz.environment_selected.v1"
    ENVIRONMENT_SELECTION_DENIED = "authz.environment_selection_denied.v1"
    CAPABILITY_PROJECTION_GENERATED = "authz.capability_projection_generated.v1"
    STEP_UP_REQUIRED = "authz.step_up_required.v1"
    ABUSE_REQUEST_THROTTLED = "security.abuse_request_throttled.v1"
    ABUSE_TEMPORARY_BLOCK_APPLIED = "security.abuse_temporary_block_applied.v1"
    ABUSE_PENALTY_ESCALATED = "security.abuse_penalty_escalated.v1"
    ABUSE_BACKEND_UNAVAILABLE = "security.abuse_backend_unavailable.v1"
    ABUSE_FALLBACK_ACTIVATED = "security.abuse_fallback_activated.v1"
    ABUSE_PROVIDER_OPERATION_THROTTLED = "security.abuse_provider_operation_throttled.v1"
    ABUSE_REPLAY_FAST_REJECTED = "security.abuse_replay_fast_rejected.v1"
    AUDIT_OUTBOX_DELIVERY_FAILED = "operations.audit_outbox_delivery_failed.v1"
    AUDIT_OUTBOX_DEAD_LETTERED = "operations.audit_outbox_dead_lettered.v1"
    AUDIT_OUTBOX_REQUEUED = "operations.audit_outbox_requeued.v1"
    AUDIT_OUTBOX_LEASE_RECOVERED = "operations.audit_outbox_lease_recovered.v1"
    MAINTENANCE_JOB_FAILED = "operations.maintenance_job_failed.v1"
    MAINTENANCE_JOB_RECOVERED = "operations.maintenance_job_recovered.v1"
    MAINTENANCE_DATA_REMOVED = "operations.maintenance_data_removed.v1"


@dataclass(frozen=True)
class AuditEvent:
    event_type: AuditEventType
    actor_type: str
    outcome: str
    correlation_id: str
    request_id: str
    event_id: UUID = field(default_factory=uuid4)
    deduplication_key: UUID = field(default_factory=uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    principal_id: UUID | None = None
    issuer_id: str | None = None
    subject_reference: str | None = None
    session_reference: str | None = None
    tenant_id: str | None = None
    environment_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_reference: str | None = None
    capability_id: str | None = None
    decision: str | None = None
    reason_code: str | None = None
    policy_revision: str | None = None
    capability_revision: str | None = None
    authentication_assurance: str | None = None
    source_network_classification: str | None = None
    user_agent_classification: str | None = None
    safe_metadata: dict[str, Any] = field(default_factory=dict)
