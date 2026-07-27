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
    SESSION_EXPIRED_IDLE = "auth.session_expired_idle.v1"
    SESSION_EXPIRED_ABSOLUTE = "auth.session_expired_absolute.v1"
    SESSION_REVOKED = "auth.session_revoked.v1"
    LOGOUT_REQUESTED = "auth.logout_requested.v1"
    LOGOUT_COMPLETED = "auth.logout_completed.v1"
    PROVIDER_LOGOUT_FAILED = "auth.provider_logout_failed.v1"
    CSRF_REJECTED = "auth.csrf_rejected.v1"
    AUTHORIZATION_ALLOWED = "authz.decision_allowed.v1"
    AUTHORIZATION_DENIED = "authz.decision_denied.v1"
    AUTHORIZATION_INDETERMINATE = "authz.decision_indeterminate.v1"
    ENVIRONMENT_SELECTED = "authz.environment_selected.v1"
    ENVIRONMENT_SELECTION_DENIED = "authz.environment_selection_denied.v1"
    CAPABILITY_PROJECTION_GENERATED = "authz.capability_projection_generated.v1"
    STEP_UP_REQUIRED = "authz.step_up_required.v1"


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
