"""Durable provider-backed session refresh, revocation, logout, and disposal."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.ports import (
    OidcProviderPort,
    ProviderExchangeFailure,
    ProviderFailureKind,
    ProviderRefreshTokenSet,
    ProviderTokenKind,
)
from portal_api.auth.protected_value import ProtectedValue, ProtectedValueCipher
from portal_api.auth.refresh_token_validation import (
    ProviderRefreshIdentityValidator,
    RefreshIdentityValidationError,
)
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.auth.session import AuthenticatedSession
from portal_api.core.config import PortalApiSettings
from portal_api.db.metadata import (
    portal_principals,
    portal_provider_logout_receipts,
    portal_sessions,
    portal_token_envelopes,
)
from portal_api.db.unit_of_work import local_transaction
from portal_api.telemetry.metrics import NoopTelemetry, TelemetryRecorder

LOGGER = logging.getLogger("portal_api.provider_session")
AUTH_TAG_BYTES = 16


class ProviderSessionState(StrEnum):
    ACTIVE = "ACTIVE"
    REFRESH_PENDING = "REFRESH_PENDING"
    REFRESHING = "REFRESHING"
    REFRESH_REQUIRED = "REFRESH_REQUIRED"
    REFRESH_FAILED = "REFRESH_FAILED"
    EXPIRED = "EXPIRED"
    LOGGED_OUT = "LOGGED_OUT"
    REVOKED = "REVOKED"
    DISPOSED = "DISPOSED"


_ALLOWED_TRANSITIONS: dict[ProviderSessionState, frozenset[ProviderSessionState]] = {
    ProviderSessionState.ACTIVE: frozenset(
        {
            ProviderSessionState.REFRESH_PENDING,
            ProviderSessionState.REFRESH_REQUIRED,
            ProviderSessionState.EXPIRED,
            ProviderSessionState.LOGGED_OUT,
            ProviderSessionState.REVOKED,
            ProviderSessionState.DISPOSED,
        }
    ),
    ProviderSessionState.REFRESH_PENDING: frozenset(
        {
            ProviderSessionState.REFRESHING,
            ProviderSessionState.REFRESH_REQUIRED,
            ProviderSessionState.LOGGED_OUT,
            ProviderSessionState.REVOKED,
            ProviderSessionState.DISPOSED,
        }
    ),
    ProviderSessionState.REFRESHING: frozenset(
        {
            ProviderSessionState.ACTIVE,
            ProviderSessionState.REFRESH_FAILED,
            ProviderSessionState.REFRESH_REQUIRED,
            ProviderSessionState.EXPIRED,
            ProviderSessionState.LOGGED_OUT,
            ProviderSessionState.REVOKED,
            ProviderSessionState.DISPOSED,
        }
    ),
    ProviderSessionState.REFRESH_FAILED: frozenset(
        {
            ProviderSessionState.REFRESH_PENDING,
            ProviderSessionState.REFRESH_REQUIRED,
            ProviderSessionState.EXPIRED,
            ProviderSessionState.LOGGED_OUT,
            ProviderSessionState.REVOKED,
            ProviderSessionState.DISPOSED,
        }
    ),
    ProviderSessionState.REFRESH_REQUIRED: frozenset(
        {
            ProviderSessionState.REFRESH_PENDING,
            ProviderSessionState.EXPIRED,
            ProviderSessionState.LOGGED_OUT,
            ProviderSessionState.REVOKED,
            ProviderSessionState.DISPOSED,
        }
    ),
    ProviderSessionState.EXPIRED: frozenset({ProviderSessionState.DISPOSED}),
    ProviderSessionState.LOGGED_OUT: frozenset({ProviderSessionState.DISPOSED}),
    ProviderSessionState.REVOKED: frozenset({ProviderSessionState.DISPOSED}),
    ProviderSessionState.DISPOSED: frozenset(),
}


def require_provider_transition(
    current: ProviderSessionState,
    target: ProviderSessionState,
) -> None:
    """Reject nondeterministic or terminal-state provider lifecycle transitions."""
    if target not in _ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Provider session transition {current.value}->{target.value} is invalid")


@dataclass(frozen=True, repr=False)
class ProviderTokens:
    id_token: str | None
    access_token: str | None
    refresh_token: str | None

    def __repr__(self) -> str:
        return (
            "ProviderTokens(id_token=<protected>, access_token=<protected>, "
            "refresh_token=<protected>)"
        )


@dataclass(frozen=True)
class RefreshClaim:
    envelope_id: UUID
    session_family_id: UUID
    worker_id: str
    token_generation: int
    refresh_failures: int
    provider_subject: str
    provider_session: str | None
    previous_refresh_token_fingerprint: bytes | None
    tokens: ProviderTokens = field(repr=False)


@dataclass(frozen=True)
class LogoutTokenTarget:
    envelope_id: UUID
    session_family_id: UUID
    tokens: ProviderTokens = field(repr=False)


@dataclass(frozen=True)
class LogoutPlan:
    revoked_session_count: int
    targets: tuple[LogoutTokenTarget, ...]
    correlation_id: str
    request_id: str


@dataclass(frozen=True)
class CleanupEvidence:
    session_family_id: UUID
    operation: str
    outcome: str
    reason_code: str | None = None


@dataclass(frozen=True)
class ProviderLogoutResult:
    revoked_session_count: int
    front_channel_logout_url: str | None
    provider_failures: int


class ProviderLogoutReplayError(RuntimeError):
    """A provider logout-token identifier was already consumed."""


class ProviderSessionRepository:
    """Transactionally coordinates provider token envelopes without network I/O."""

    def __init__(
        self,
        *,
        engine: Engine,
        settings: PortalApiSettings,
        security_material: EphemeralSecurityMaterial,
        protected_value_cipher: ProtectedValueCipher,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._security_material = security_material
        self._cipher = protected_value_cipher
        self._audit = audit_ledger or AuditLedger()

    def claim_due(
        self,
        *,
        worker_id: str,
        correlation_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> tuple[RefreshClaim, ...]:
        evaluated_at = now or datetime.now(UTC)
        refresh_before = evaluated_at + timedelta(
            seconds=self._settings.provider_refresh_threshold_seconds
        )
        lease_until = evaluated_at + timedelta(
            seconds=self._settings.provider_refresh_lease_seconds
        )
        claims: list[RefreshClaim] = []
        with local_transaction(self._engine) as connection:
            due = (
                connection.execute(
                    select(portal_token_envelopes)
                    .where(
                        portal_token_envelopes.c.disposed_at.is_(None),
                        or_(
                            and_(
                                portal_token_envelopes.c.lifecycle_state == "ACTIVE",
                                portal_token_envelopes.c.expires_at.is_not(None),
                                portal_token_envelopes.c.expires_at <= refresh_before,
                            ),
                            and_(
                                portal_token_envelopes.c.lifecycle_state == "REFRESH_FAILED",
                                portal_token_envelopes.c.next_refresh_attempt_at.is_not(None),
                                portal_token_envelopes.c.next_refresh_attempt_at <= evaluated_at,
                            ),
                            and_(
                                portal_token_envelopes.c.lifecycle_state == "REFRESH_REQUIRED",
                                or_(
                                    portal_token_envelopes.c.next_refresh_attempt_at.is_(None),
                                    portal_token_envelopes.c.next_refresh_attempt_at
                                    <= evaluated_at,
                                ),
                            ),
                            and_(
                                portal_token_envelopes.c.lifecycle_state == "REFRESHING",
                                portal_token_envelopes.c.refresh_lease_expires_at.is_not(None),
                                portal_token_envelopes.c.refresh_lease_expires_at <= evaluated_at,
                            ),
                        ),
                    )
                    .order_by(
                        portal_token_envelopes.c.next_refresh_attempt_at.asc().nullsfirst(),
                        portal_token_envelopes.c.expires_at.asc().nullsfirst(),
                    )
                    .limit(self._settings.provider_refresh_batch_size)
                    .with_for_update(skip_locked=True)
                )
                .mappings()
                .all()
            )
            for durable_row in due:
                row = dict(durable_row)
                current = ProviderSessionState(str(row["lifecycle_state"]))
                if current is ProviderSessionState.REFRESHING:
                    self._transition(
                        connection,
                        row=row,
                        target=ProviderSessionState.REFRESH_FAILED,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code="REFRESH_LEASE_EXPIRED",
                    )
                    row["refresh_failures"] = int(row["refresh_failures"]) + 1
                    connection.execute(
                        update(portal_token_envelopes)
                        .where(portal_token_envelopes.c.envelope_id == row["envelope_id"])
                        .values(refresh_failures=row["refresh_failures"])
                    )

                active_session = (
                    connection.execute(
                        select(portal_sessions)
                        .where(
                            portal_sessions.c.session_family_id == row["session_family_id"],
                            portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
                        )
                        .order_by(portal_sessions.c.created_at.desc())
                        .limit(1)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if active_session is None:
                    self._dispose(
                        connection,
                        row=row,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code="NO_ACTIVE_LOCAL_SESSION",
                    )
                    continue
                local_expiration: tuple[str, str] | None = None
                if active_session["absolute_expires_at"] <= evaluated_at:
                    local_expiration = ("EXPIRED_ABSOLUTE", "SESSION_ABSOLUTE_EXPIRED")
                elif active_session["idle_expires_at"] <= evaluated_at:
                    local_expiration = ("EXPIRED_IDLE", "SESSION_IDLE_EXPIRED")
                if local_expiration is not None:
                    session_status, reason_code = local_expiration
                    self._fail_closed_sessions(
                        connection,
                        session_family_id=row["session_family_id"],
                        status=session_status,
                        reason_code=reason_code,
                        now=evaluated_at,
                    )
                    self._transition(
                        connection,
                        row=row,
                        target=ProviderSessionState.EXPIRED,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code=reason_code,
                    )
                    self._dispose(
                        connection,
                        row=row,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code=reason_code,
                    )
                    continue

                refresh_expires_at = row["refresh_expires_at"]
                if refresh_expires_at is not None and refresh_expires_at <= evaluated_at:
                    self._transition(
                        connection,
                        row=row,
                        target=ProviderSessionState.EXPIRED,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code="REFRESH_TOKEN_EXPIRED",
                    )
                    self._fail_closed_sessions(
                        connection,
                        session_family_id=row["session_family_id"],
                        status="PROVIDER_REVOKED",
                        reason_code="REFRESH_TOKEN_EXPIRED",
                        now=evaluated_at,
                    )
                    self._dispose(
                        connection,
                        row=row,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code="REFRESH_TOKEN_EXPIRED",
                    )
                    continue
                try:
                    tokens = self._decrypt_tokens(row)
                except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                    self._transition(
                        connection,
                        row=row,
                        target=ProviderSessionState.REFRESH_REQUIRED,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code="TOKEN_ENVELOPE_UNAVAILABLE",
                    )
                    self._fail_closed_sessions(
                        connection,
                        session_family_id=row["session_family_id"],
                        status="REFRESH_REQUIRED",
                        reason_code="TOKEN_ENVELOPE_UNAVAILABLE",
                        now=evaluated_at,
                    )
                    continue
                if tokens.refresh_token is None:
                    self._transition(
                        connection,
                        row=row,
                        target=ProviderSessionState.REFRESH_REQUIRED,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code="REFRESH_TOKEN_UNAVAILABLE",
                    )
                    self._fail_closed_sessions(
                        connection,
                        session_family_id=row["session_family_id"],
                        status="REFRESH_REQUIRED",
                        reason_code="REFRESH_TOKEN_UNAVAILABLE",
                        now=evaluated_at,
                    )
                    continue
                self._transition(
                    connection,
                    row=row,
                    target=ProviderSessionState.REFRESH_PENDING,
                    now=evaluated_at,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code="PROACTIVE_REFRESH_DUE",
                )
                self._transition(
                    connection,
                    row=row,
                    target=ProviderSessionState.REFRESHING,
                    now=evaluated_at,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code="REFRESH_CLAIMED",
                    values={
                        "refresh_lease_owner": worker_id,
                        "refresh_lease_expires_at": lease_until,
                        "next_refresh_attempt_at": None,
                    },
                )
                self._append(
                    connection,
                    row=row,
                    event_type=AuditEventType.PROVIDER_REFRESH_STARTED,
                    outcome="STARTED",
                    reason_code="PROACTIVE_REFRESH_DUE",
                    correlation_id=correlation_id,
                    request_id=request_id,
                    safe_metadata={
                        "token_generation": int(row["token_generation"]),
                        "retry_count": int(row["refresh_failures"]),
                    },
                )
                claims.append(
                    RefreshClaim(
                        envelope_id=row["envelope_id"],
                        session_family_id=row["session_family_id"],
                        worker_id=worker_id,
                        token_generation=int(row["token_generation"]),
                        refresh_failures=int(row["refresh_failures"]),
                        provider_subject=str(row["provider_subject"]),
                        provider_session=(
                            str(row["provider_session"])
                            if row["provider_session"] is not None
                            else None
                        ),
                        previous_refresh_token_fingerprint=row[
                            "previous_refresh_token_fingerprint"
                        ],
                        tokens=tokens,
                    )
                )
        return tuple(claims)

    def complete_refresh(
        self,
        *,
        claim: RefreshClaim,
        refreshed: ProviderRefreshTokenSet,
        correlation_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> str:
        evaluated_at = now or datetime.now(UTC)
        with local_transaction(self._engine) as connection:
            durable = (
                connection.execute(
                    select(portal_token_envelopes)
                    .where(portal_token_envelopes.c.envelope_id == claim.envelope_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                durable is None
                or durable["lifecycle_state"] != "REFRESHING"
                or durable["refresh_lease_owner"] != claim.worker_id
                or int(durable["token_generation"]) != claim.token_generation
            ):
                return "stale_claim"
            row = dict(durable)
            next_refresh_token = refreshed.refresh_token or claim.tokens.refresh_token
            if next_refresh_token is None:
                raise RuntimeError("Provider refresh removed the required refresh token")
            next_fingerprint = self._fingerprint(next_refresh_token)
            previous_fingerprint = row["previous_refresh_token_fingerprint"]
            if previous_fingerprint is not None and hmac.compare_digest(
                previous_fingerprint,
                next_fingerprint,
            ):
                self._append(
                    connection,
                    row=row,
                    event_type=AuditEventType.PROVIDER_REFRESH_REUSE_DETECTED,
                    outcome="REJECTED",
                    reason_code="REFRESH_TOKEN_REUSE_DETECTED",
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
                self._transition(
                    connection,
                    row=row,
                    target=ProviderSessionState.REVOKED,
                    now=evaluated_at,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code="REFRESH_TOKEN_REUSE_DETECTED",
                )
                self._fail_closed_sessions(
                    connection,
                    session_family_id=claim.session_family_id,
                    status="PROVIDER_REVOKED",
                    reason_code="REFRESH_TOKEN_REUSE_DETECTED",
                    now=evaluated_at,
                )
                self._dispose(
                    connection,
                    row=row,
                    now=evaluated_at,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code="REFRESH_TOKEN_REUSE_DETECTED",
                )
                return "reuse_detected"

            rotated = not hmac.compare_digest(
                row["refresh_token_fingerprint"] or b"",
                next_fingerprint,
            )
            next_tokens = ProviderTokens(
                id_token=refreshed.id_token or claim.tokens.id_token,
                access_token=refreshed.access_token,
                refresh_token=next_refresh_token,
            )
            protected_fields = self._encrypt_tokens(
                next_tokens,
                session_family_id=claim.session_family_id,
            )
            expires_at = evaluated_at + timedelta(seconds=refreshed.expires_in)
            refresh_expires_at = (
                evaluated_at + timedelta(seconds=refreshed.refresh_expires_in)
                if refreshed.refresh_expires_in is not None
                else row["refresh_expires_at"]
            )
            old_fingerprint = row["refresh_token_fingerprint"]
            self._transition(
                connection,
                row=row,
                target=ProviderSessionState.ACTIVE,
                now=evaluated_at,
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code="PROVIDER_REFRESH_SUCCEEDED",
                values={
                    **protected_fields,
                    "token_generation": claim.token_generation + 1,
                    "refresh_token_fingerprint": next_fingerprint,
                    "previous_refresh_token_fingerprint": (
                        old_fingerprint if rotated else previous_fingerprint
                    ),
                    "refresh_failures": 0,
                    "refresh_lease_owner": None,
                    "refresh_lease_expires_at": None,
                    "next_refresh_attempt_at": None,
                    "last_failure_code": None,
                    "expires_at": expires_at,
                    "refresh_expires_at": refresh_expires_at,
                    "refreshed_at": evaluated_at,
                    "rotated_at": evaluated_at if rotated else row["rotated_at"],
                },
            )
            sessions = (
                connection.execute(
                    select(portal_sessions)
                    .where(
                        portal_sessions.c.session_family_id == claim.session_family_id,
                        portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
                    )
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            for session in sessions:
                identity_until = min(
                    evaluated_at + timedelta(seconds=self._settings.identity_freshness_seconds),
                    expires_at,
                    session["absolute_expires_at"],
                )
                connection.execute(
                    update(portal_sessions)
                    .where(
                        portal_sessions.c.session_id == session["session_id"],
                        portal_sessions.c.version == session["version"],
                    )
                    .values(
                        status="ACTIVE",
                        provider_expires_at=expires_at,
                        identity_verified_until=identity_until,
                        last_refresh_at=evaluated_at,
                        version=int(session["version"]) + 1,
                    )
                )
            self._append(
                connection,
                row=row,
                event_type=AuditEventType.PROVIDER_REFRESH_SUCCEEDED,
                outcome="SUCCEEDED",
                reason_code="REFRESH_TOKEN_ROTATED" if rotated else "REFRESH_TOKEN_RETAINED",
                correlation_id=correlation_id,
                request_id=request_id,
                safe_metadata={
                    "token_generation": claim.token_generation + 1,
                    "refresh_rotated": rotated,
                },
            )
            self._append(
                connection,
                row=row,
                event_type=AuditEventType.SESSION_REFRESHED,
                outcome="REFRESHED",
                reason_code="PROVIDER_REFRESH_SUCCEEDED",
                correlation_id=correlation_id,
                request_id=request_id,
            )
        return "rotated" if rotated else "refreshed"

    def fail_refresh(
        self,
        *,
        claim: RefreshClaim,
        failure: ProviderExchangeFailure,
        correlation_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> str:
        evaluated_at = now or datetime.now(UTC)
        with local_transaction(self._engine) as connection:
            durable = (
                connection.execute(
                    select(portal_token_envelopes)
                    .where(portal_token_envelopes.c.envelope_id == claim.envelope_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                durable is None
                or durable["lifecycle_state"] != "REFRESHING"
                or durable["refresh_lease_owner"] != claim.worker_id
                or int(durable["token_generation"]) != claim.token_generation
            ):
                return "stale_claim"
            row = dict(durable)
            failures = int(row["refresh_failures"]) + 1
            if failure.reason_code == "INVALID_GRANT":
                self._transition(
                    connection,
                    row=row,
                    target=ProviderSessionState.REVOKED,
                    now=evaluated_at,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code="INVALID_GRANT",
                )
                self._fail_closed_sessions(
                    connection,
                    session_family_id=claim.session_family_id,
                    status="PROVIDER_REVOKED",
                    reason_code="INVALID_GRANT",
                    now=evaluated_at,
                )
                self._append(
                    connection,
                    row=row,
                    event_type=AuditEventType.PROVIDER_REFRESH_FAILED,
                    outcome="REVOKED",
                    reason_code="INVALID_GRANT",
                    correlation_id=correlation_id,
                    request_id=request_id,
                    safe_metadata={"retry_count": failures},
                )
                self._dispose(
                    connection,
                    row=row,
                    now=evaluated_at,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code="INVALID_GRANT",
                )
                return "revoked"

            expires_at = row["expires_at"]
            retryable = (
                failure.kind is ProviderFailureKind.AMBIGUOUS
                and failures < self._settings.provider_refresh_retry_budget
                and (expires_at is None or expires_at > evaluated_at)
            )
            target = (
                ProviderSessionState.REFRESH_FAILED
                if retryable
                else ProviderSessionState.REFRESH_REQUIRED
            )
            backoff = min(
                self._settings.provider_refresh_initial_backoff_seconds
                * (2 ** max(0, failures - 1)),
                self._settings.provider_refresh_max_backoff_seconds,
            )
            self._transition(
                connection,
                row=row,
                target=target,
                now=evaluated_at,
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=failure.reason_code,
                values={
                    "refresh_failures": failures,
                    "refresh_lease_owner": None,
                    "refresh_lease_expires_at": None,
                    "next_refresh_attempt_at": (
                        evaluated_at + timedelta(seconds=backoff) if retryable else None
                    ),
                    "last_failure_code": failure.reason_code,
                },
            )
            if not retryable:
                self._fail_closed_sessions(
                    connection,
                    session_family_id=claim.session_family_id,
                    status="REFRESH_REQUIRED",
                    reason_code=failure.reason_code,
                    now=evaluated_at,
                )
            self._append(
                connection,
                row=row,
                event_type=AuditEventType.PROVIDER_REFRESH_FAILED,
                outcome="RETRY_SCHEDULED" if retryable else "FAILED_CLOSED",
                reason_code=failure.reason_code,
                correlation_id=correlation_id,
                request_id=request_id,
                safe_metadata={
                    "retry_count": failures,
                    "retry_scheduled": retryable,
                },
            )
        return "retry_scheduled" if retryable else "failed_closed"

    def begin_logout(
        self,
        *,
        session: AuthenticatedSession,
        all_for_principal: bool,
        correlation_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> LogoutPlan:
        evaluated_at = now or datetime.now(UTC)
        targets: list[LogoutTokenTarget] = []
        revoked = 0
        with local_transaction(self._engine) as connection:
            principal = (
                connection.execute(
                    select(portal_principals)
                    .where(portal_principals.c.principal_id == session.principal_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if principal is None:
                return LogoutPlan(0, (), correlation_id, request_id)
            if all_for_principal:
                connection.execute(
                    update(portal_principals)
                    .where(portal_principals.c.principal_id == session.principal_id)
                    .values(sessions_valid_after=evaluated_at, updated_at=evaluated_at)
                )
            query = select(portal_sessions).where(
                portal_sessions.c.principal_id == session.principal_id,
                portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
            )
            if not all_for_principal:
                query = query.where(
                    portal_sessions.c.session_family_id == session.session_family_id
                )
            sessions = (
                connection.execute(query.order_by(portal_sessions.c.created_at).with_for_update())
                .mappings()
                .all()
            )
            families = {durable["session_family_id"] for durable in sessions}
            for durable in sessions:
                result = connection.execute(
                    update(portal_sessions)
                    .where(
                        portal_sessions.c.session_id == durable["session_id"],
                        portal_sessions.c.version == durable["version"],
                        portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
                    )
                    .values(
                        status="REVOKED",
                        revoked_at=evaluated_at,
                        revoked_reason="LOGOUT_ALL" if all_for_principal else "LOGOUT",
                        version=int(durable["version"]) + 1,
                    )
                )
                revoked += int(result.rowcount or 0)
            if families:
                envelopes = (
                    connection.execute(
                        select(portal_token_envelopes)
                        .where(
                            portal_token_envelopes.c.session_family_id.in_(families),
                            portal_token_envelopes.c.disposed_at.is_(None),
                        )
                        .with_for_update()
                    )
                    .mappings()
                    .all()
                )
                for durable_envelope in envelopes:
                    row = dict(durable_envelope)
                    try:
                        tokens = self._decrypt_tokens(row)
                    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
                        tokens = ProviderTokens(None, None, None)
                    current = ProviderSessionState(str(row["lifecycle_state"]))
                    if current is not ProviderSessionState.LOGGED_OUT:
                        self._transition(
                            connection,
                            row=row,
                            target=ProviderSessionState.LOGGED_OUT,
                            now=evaluated_at,
                            correlation_id=correlation_id,
                            request_id=request_id,
                            reason_code="LOCAL_SESSION_TERMINATED",
                            values={
                                "refresh_lease_owner": None,
                                "refresh_lease_expires_at": None,
                            },
                        )
                    targets.append(
                        LogoutTokenTarget(
                            envelope_id=row["envelope_id"],
                            session_family_id=row["session_family_id"],
                            tokens=tokens,
                        )
                    )
            self._append_session_event(
                connection,
                session=session,
                event_type=AuditEventType.LOGOUT_REQUESTED,
                outcome="REQUESTED",
                reason_code="LOGOUT_ALL" if all_for_principal else "LOGOUT",
                correlation_id=correlation_id,
                request_id=request_id,
                safe_metadata={
                    "scope": "all" if all_for_principal else "current",
                    "revoked_session_count": revoked,
                },
            )
        return LogoutPlan(revoked, tuple(targets), correlation_id, request_id)

    def finish_logout(
        self,
        *,
        plan: LogoutPlan,
        evidence: tuple[CleanupEvidence, ...],
        now: datetime | None = None,
    ) -> None:
        evaluated_at = now or datetime.now(UTC)
        evidence_by_family: dict[UUID, list[CleanupEvidence]] = {}
        for item in evidence:
            evidence_by_family.setdefault(item.session_family_id, []).append(item)
        with local_transaction(self._engine) as connection:
            for target in plan.targets:
                durable = (
                    connection.execute(
                        select(portal_token_envelopes)
                        .where(portal_token_envelopes.c.envelope_id == target.envelope_id)
                        .with_for_update()
                    )
                    .mappings()
                    .one_or_none()
                )
                if durable is not None and durable["disposed_at"] is None:
                    row = dict(durable)
                    self._dispose(
                        connection,
                        row=row,
                        now=evaluated_at,
                        correlation_id=plan.correlation_id,
                        request_id=plan.request_id,
                        reason_code="LOGOUT_TOKEN_DISPOSAL",
                    )
                connection.execute(
                    update(portal_sessions)
                    .where(
                        portal_sessions.c.session_family_id == target.session_family_id,
                        portal_sessions.c.status.in_(
                            (
                                "REVOKED",
                                "PROVIDER_REVOKED",
                                "INVALID",
                                "EXPIRED_IDLE",
                                "EXPIRED_ABSOLUTE",
                            )
                        ),
                    )
                    .values(status="TERMINATED")
                )
                for item in evidence_by_family.get(target.session_family_id, []):
                    event_type = self._cleanup_event_type(item)
                    self._append(
                        connection,
                        row=(
                            dict(durable)
                            if durable is not None
                            else {"session_family_id": target.session_family_id}
                        ),
                        event_type=event_type,
                        outcome=item.outcome,
                        reason_code=item.reason_code,
                        correlation_id=plan.correlation_id,
                        request_id=plan.request_id,
                        safe_metadata={"operation": item.operation},
                    )
            representative = self._representative_session(
                connection,
                plan.targets[0].session_family_id if plan.targets else None,
            )
            self._append(
                connection,
                row=representative or {},
                event_type=AuditEventType.LOGOUT_COMPLETED,
                outcome="COMPLETED",
                reason_code=None,
                correlation_id=plan.correlation_id,
                request_id=plan.request_id,
                safe_metadata={
                    "revoked_session_count": plan.revoked_session_count,
                    "provider_failure_count": sum(
                        1 for item in evidence if item.outcome == "FAILED"
                    ),
                },
            )

    def revoke_from_backchannel(
        self,
        *,
        provider_session: str | None,
        provider_subject: str | None,
        token_identifier: str,
        issued_at: datetime,
        correlation_id: str,
        request_id: str,
        now: datetime | None = None,
    ) -> int:
        if provider_session is None and provider_subject is None:
            return 0
        evaluated_at = now or datetime.now(UTC)
        revoked = 0
        with local_transaction(self._engine) as connection:
            connection.execute(
                delete(portal_provider_logout_receipts).where(
                    portal_provider_logout_receipts.c.expires_at <= evaluated_at
                )
            )
            receipt = connection.execute(
                postgresql_insert(portal_provider_logout_receipts)
                .values(
                    receipt_id=uuid4(),
                    provider_id=self._settings.oidc_provider_id,
                    jti_hash=self._security_material.protect(
                        token_identifier,
                        purpose=ProtectedPurpose.PROVIDER_LOGOUT_JTI,
                    ),
                    issued_at=issued_at,
                    expires_at=issued_at
                    + timedelta(seconds=self._settings.provider_logout_replay_ttl_seconds),
                    created_at=evaluated_at,
                )
                .on_conflict_do_nothing(index_elements=[portal_provider_logout_receipts.c.jti_hash])
                .returning(portal_provider_logout_receipts.c.receipt_id)
            )
            if receipt.scalar_one_or_none() is None:
                raise ProviderLogoutReplayError("Provider logout token has already been consumed")
            identity_predicates = []
            if provider_session is not None:
                identity_predicates.append(
                    portal_token_envelopes.c.provider_session == provider_session
                )
            if provider_subject is not None:
                identity_predicates.append(
                    portal_token_envelopes.c.provider_subject == provider_subject
                )
            envelopes = (
                connection.execute(
                    select(portal_token_envelopes)
                    .where(
                        or_(*identity_predicates),
                        portal_token_envelopes.c.disposed_at.is_(None),
                        portal_token_envelopes.c.lifecycle_state.in_(
                            (
                                "ACTIVE",
                                "REFRESH_PENDING",
                                "REFRESHING",
                                "REFRESH_REQUIRED",
                                "REFRESH_FAILED",
                            )
                        ),
                    )
                    .with_for_update()
                )
                .mappings()
                .all()
            )
            for durable in envelopes:
                row = dict(durable)
                result = connection.execute(
                    update(portal_sessions)
                    .where(
                        portal_sessions.c.session_family_id == row["session_family_id"],
                        portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
                    )
                    .values(
                        status="PROVIDER_REVOKED",
                        revoked_at=evaluated_at,
                        revoked_reason="PROVIDER_BACKCHANNEL_LOGOUT",
                        version=portal_sessions.c.version + 1,
                    )
                )
                revoked += int(result.rowcount or 0)
                current = ProviderSessionState(str(row["lifecycle_state"]))
                if current is not ProviderSessionState.REVOKED:
                    self._transition(
                        connection,
                        row=row,
                        target=ProviderSessionState.REVOKED,
                        now=evaluated_at,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        reason_code="PROVIDER_BACKCHANNEL_LOGOUT",
                    )
                self._append(
                    connection,
                    row=row,
                    event_type=AuditEventType.PROVIDER_BACKCHANNEL_LOGOUT,
                    outcome="REVOKED",
                    reason_code="PROVIDER_BACKCHANNEL_LOGOUT",
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
                self._dispose(
                    connection,
                    row=row,
                    now=evaluated_at,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    reason_code="PROVIDER_BACKCHANNEL_LOGOUT",
                )
        return revoked

    def _transition(
        self,
        connection: Connection,
        *,
        row: dict[str, Any],
        target: ProviderSessionState,
        now: datetime,
        correlation_id: str,
        request_id: str,
        reason_code: str,
        values: Mapping[str, Any] | None = None,
    ) -> None:
        current = ProviderSessionState(str(row["lifecycle_state"]))
        require_provider_transition(current, target)
        updates = {
            "lifecycle_state": target.value,
            "updated_at": now,
            **dict(values or {}),
        }
        result = connection.execute(
            update(portal_token_envelopes)
            .where(
                portal_token_envelopes.c.envelope_id == row["envelope_id"],
                portal_token_envelopes.c.lifecycle_state == current.value,
            )
            .values(**updates)
        )
        if result.rowcount != 1:
            raise RuntimeError("Provider session transition lost its durable authority")
        row.update(updates)
        self._append(
            connection,
            row=row,
            event_type=AuditEventType.PROVIDER_SESSION_STATE_CHANGED,
            outcome=target.value,
            reason_code=reason_code,
            correlation_id=correlation_id,
            request_id=request_id,
            safe_metadata={
                "from_state": current.value,
                "to_state": target.value,
            },
        )

    def _dispose(
        self,
        connection: Connection,
        *,
        row: dict[str, Any],
        now: datetime,
        correlation_id: str,
        request_id: str,
        reason_code: str,
    ) -> None:
        current = ProviderSessionState(str(row["lifecycle_state"]))
        if current is ProviderSessionState.DISPOSED:
            return
        self._transition(
            connection,
            row=row,
            target=ProviderSessionState.DISPOSED,
            now=now,
            correlation_id=correlation_id,
            request_id=request_id,
            reason_code=reason_code,
            values={
                "ciphertext": os.urandom(32),
                "nonce": os.urandom(12),
                "authentication_tag": os.urandom(AUTH_TAG_BYTES),
                "wrapped_data_key": os.urandom(32),
                "wrapped_data_key_nonce": os.urandom(12),
                "refresh_token_fingerprint": None,
                "previous_refresh_token_fingerprint": None,
                "refresh_lease_owner": None,
                "refresh_lease_expires_at": None,
                "next_refresh_attempt_at": None,
                "disposed_at": now,
            },
        )
        self._append(
            connection,
            row=row,
            event_type=AuditEventType.PROVIDER_TOKEN_DISPOSED,
            outcome="DISPOSED",
            reason_code=reason_code,
            correlation_id=correlation_id,
            request_id=request_id,
        )

    def _fail_closed_sessions(
        self,
        connection: Connection,
        *,
        session_family_id: UUID,
        status: str,
        reason_code: str,
        now: datetime,
    ) -> None:
        values: dict[str, Any] = {
            "status": status,
            "revoked_reason": reason_code,
            "version": portal_sessions.c.version + 1,
        }
        if status == "PROVIDER_REVOKED":
            values["revoked_at"] = now
        connection.execute(
            update(portal_sessions)
            .where(
                portal_sessions.c.session_family_id == session_family_id,
                portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
            )
            .values(**values)
        )

    def _decrypt_tokens(self, row: Mapping[str, Any]) -> ProviderTokens:
        combined = bytes(row["ciphertext"]) + bytes(row["authentication_tag"])
        protected = ProtectedValue(
            ciphertext=base64.urlsafe_b64encode(combined).decode("ascii"),
            nonce=base64.urlsafe_b64encode(bytes(row["nonce"])).decode("ascii"),
            wrapped_data_key=base64.urlsafe_b64encode(bytes(row["wrapped_data_key"])).decode(
                "ascii"
            ),
            wrapped_data_key_nonce=base64.urlsafe_b64encode(
                bytes(row["wrapped_data_key_nonce"])
            ).decode("ascii"),
            key_reference=str(row["kms_key_id"]),
        )
        plaintext = self._cipher.decrypt(
            protected,
            context=str(row["session_family_id"]).encode("ascii"),
        )
        payload = json.loads(plaintext)
        if not isinstance(payload, dict):
            raise ValueError("Provider token envelope payload is invalid")
        values: dict[str, str | None] = {}
        for name in ("id_token", "access_token", "refresh_token"):
            value = payload.get(name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ValueError("Provider token envelope field is invalid")
            values[name] = value
        return ProviderTokens(**values)

    def _encrypt_tokens(
        self,
        tokens: ProviderTokens,
        *,
        session_family_id: UUID,
    ) -> dict[str, bytes | str]:
        payload = {
            name: value
            for name, value in (
                ("id_token", tokens.id_token),
                ("access_token", tokens.access_token),
                ("refresh_token", tokens.refresh_token),
            )
            if value is not None
        }
        protected = self._cipher.encrypt(
            json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            context=str(session_family_id).encode("ascii"),
        )
        combined = base64.urlsafe_b64decode(protected.ciphertext.encode("ascii"))
        if len(combined) <= AUTH_TAG_BYTES:
            raise RuntimeError("Protected provider token envelope is invalid")
        return {
            "ciphertext": combined[:-AUTH_TAG_BYTES],
            "authentication_tag": combined[-AUTH_TAG_BYTES:],
            "nonce": base64.urlsafe_b64decode(protected.nonce.encode("ascii")),
            "wrapped_data_key": base64.urlsafe_b64decode(
                protected.wrapped_data_key.encode("ascii")
            ),
            "wrapped_data_key_nonce": base64.urlsafe_b64decode(
                protected.wrapped_data_key_nonce.encode("ascii")
            ),
            "kms_key_id": protected.key_reference,
        }

    def _fingerprint(self, refresh_token: str) -> bytes:
        return self._security_material.protect(
            refresh_token,
            purpose=ProtectedPurpose.PROVIDER_REFRESH_TOKEN,
        )

    def _append(
        self,
        connection: Connection,
        *,
        row: Mapping[str, Any],
        event_type: AuditEventType,
        outcome: str,
        reason_code: str | None,
        correlation_id: str,
        request_id: str,
        safe_metadata: dict[str, Any] | None = None,
    ) -> None:
        representative = self._representative_session(
            connection,
            row.get("session_family_id"),
        )
        self._audit.append(
            connection,
            AuditEvent(
                event_type=event_type,
                actor_type="SYSTEM",
                principal_id=(
                    representative["principal_id"] if representative is not None else None
                ),
                session_reference=(
                    str(representative["session_id"]) if representative is not None else None
                ),
                tenant_id=(
                    str(representative["tenant_id"]) if representative is not None else None
                ),
                action="portal.provider_session.lifecycle",
                reason_code=reason_code,
                correlation_id=correlation_id,
                request_id=request_id,
                outcome=outcome,
                safe_metadata=safe_metadata or {},
            ),
        )

    def _append_session_event(
        self,
        connection: Connection,
        *,
        session: AuthenticatedSession,
        event_type: AuditEventType,
        outcome: str,
        reason_code: str | None,
        correlation_id: str,
        request_id: str,
        safe_metadata: dict[str, Any] | None = None,
    ) -> None:
        self._audit.append(
            connection,
            AuditEvent(
                event_type=event_type,
                actor_type="PRINCIPAL",
                principal_id=session.principal_id,
                session_reference=str(session.session_id),
                tenant_id=session.tenant_id,
                action="portal.logout",
                reason_code=reason_code,
                correlation_id=correlation_id,
                request_id=request_id,
                outcome=outcome,
                safe_metadata=safe_metadata or {},
            ),
        )

    @staticmethod
    def _representative_session(
        connection: Connection,
        session_family_id: object,
    ) -> Mapping[str, Any] | None:
        if not isinstance(session_family_id, UUID):
            return None
        row = (
            connection.execute(
                select(portal_sessions)
                .where(portal_sessions.c.session_family_id == session_family_id)
                .order_by(portal_sessions.c.created_at.desc())
                .limit(1)
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row is not None else None

    @staticmethod
    def _cleanup_event_type(evidence: CleanupEvidence) -> AuditEventType:
        if evidence.operation == "provider_logout":
            return (
                AuditEventType.PROVIDER_LOGOUT_SUCCEEDED
                if evidence.outcome in {"SUCCEEDED", "UNSUPPORTED"}
                else AuditEventType.PROVIDER_LOGOUT_FAILED
            )
        return (
            AuditEventType.PROVIDER_REVOCATION_SUCCEEDED
            if evidence.outcome in {"SUCCEEDED", "UNSUPPORTED"}
            else AuditEventType.PROVIDER_REVOCATION_FAILED
        )


class ProviderSessionLifecycleService:
    """Executes provider I/O outside database transactions and finalizes atomically."""

    def __init__(
        self,
        *,
        repository: ProviderSessionRepository,
        provider: OidcProviderPort,
        settings: PortalApiSettings,
        telemetry: TelemetryRecorder | None = None,
        refresh_identity_validator: ProviderRefreshIdentityValidator | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._settings = settings
        self._telemetry = telemetry or NoopTelemetry()
        self._refresh_identity_validator = refresh_identity_validator

    async def refresh_due(self, *, worker_id: str) -> int:
        correlation_id = f"provider-refresh-{uuid4()}"
        request_id = correlation_id
        claims = await asyncio.to_thread(
            self._repository.claim_due,
            worker_id=worker_id,
            correlation_id=correlation_id,
            request_id=request_id,
        )
        if not claims:
            return 0
        await asyncio.gather(
            *(
                self._refresh_claim(
                    claim,
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
                for claim in claims
            )
        )
        return len(claims)

    async def _refresh_claim(
        self,
        claim: RefreshClaim,
        *,
        correlation_id: str,
        request_id: str,
    ) -> None:
        with self._telemetry.span(
            "provider.session.refresh",
            attributes={
                "provider.session.operation": "refresh",
                "provider.session.state": ProviderSessionState.REFRESHING.value,
                "provider.session.retry_count": claim.refresh_failures,
            },
        ):
            await self._execute_refresh_claim(
                claim,
                correlation_id=correlation_id,
                request_id=request_id,
            )

    async def _execute_refresh_claim(
        self,
        claim: RefreshClaim,
        *,
        correlation_id: str,
        request_id: str,
    ) -> None:
        started = perf_counter()
        outcome = "error"
        try:
            if claim.tokens.refresh_token is None:
                raise ProviderExchangeFailure(
                    ProviderFailureKind.AUTHORITATIVE_REJECTION,
                    reason_code="REFRESH_TOKEN_UNAVAILABLE",
                )
            async with asyncio.timeout(self._settings.oidc_http_timeout_seconds):
                refreshed = await self._provider.refresh_tokens(
                    refresh_token=claim.tokens.refresh_token
                )
            if refreshed.id_token is not None:
                if self._refresh_identity_validator is None:
                    raise ProviderExchangeFailure(
                        ProviderFailureKind.AUTHORITATIVE_REJECTION,
                        reason_code="REFRESH_IDENTITY_VALIDATOR_UNAVAILABLE",
                    )
                try:
                    await self._refresh_identity_validator.validate(
                        id_token=refreshed.id_token,
                        expected_subject=claim.provider_subject,
                        expected_provider_session=claim.provider_session,
                    )
                except RefreshIdentityValidationError as error:
                    raise ProviderExchangeFailure(
                        ProviderFailureKind.AUTHORITATIVE_REJECTION,
                        reason_code="REFRESH_IDENTITY_INVALID",
                    ) from error
            outcome = await asyncio.to_thread(
                self._repository.complete_refresh,
                claim=claim,
                refreshed=refreshed,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        except TimeoutError:
            timeout_failure = ProviderExchangeFailure(
                ProviderFailureKind.AMBIGUOUS,
                reason_code="PROVIDER_TIMEOUT",
            )
            outcome = await asyncio.to_thread(
                self._repository.fail_refresh,
                claim=claim,
                failure=timeout_failure,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        except ProviderExchangeFailure as provider_failure:
            outcome = await asyncio.to_thread(
                self._repository.fail_refresh,
                claim=claim,
                failure=provider_failure,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        except Exception:
            LOGGER.exception(
                "provider refresh failed unexpectedly",
                extra={
                    "event": "provider_refresh_unexpected_failure",
                    "session_family": str(claim.session_family_id),
                },
            )
            internal_failure = ProviderExchangeFailure(
                ProviderFailureKind.AMBIGUOUS,
                reason_code="PROVIDER_REFRESH_INTERNAL_ERROR",
            )
            outcome = await asyncio.to_thread(
                self._repository.fail_refresh,
                claim=claim,
                failure=internal_failure,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        finally:
            self._telemetry.record_provider_session(
                "refresh",
                outcome,
                (perf_counter() - started) * 1000,
                retry_count=claim.refresh_failures,
                from_state=ProviderSessionState.REFRESHING.value,
                to_state=self._outcome_state(outcome),
            )

    async def logout(
        self,
        *,
        session: AuthenticatedSession,
        all_for_principal: bool,
        correlation_id: str,
        request_id: str,
    ) -> ProviderLogoutResult:
        plan = await asyncio.to_thread(
            self._repository.begin_logout,
            session=session,
            all_for_principal=all_for_principal,
            correlation_id=correlation_id,
            request_id=request_id,
        )
        evidence: list[CleanupEvidence] = []
        for target in plan.targets:
            evidence.extend(await self._cleanup_provider(target))
        front_channel_url: str | None = None
        try:
            async with asyncio.timeout(self._settings.provider_logout_timeout_seconds):
                front_channel_url = await self._provider.front_channel_logout_url()
        except (TimeoutError, ProviderExchangeFailure):
            LOGGER.warning(
                "provider front-channel logout metadata unavailable",
                extra={"event": "provider_front_channel_logout_unavailable"},
            )
        await asyncio.to_thread(
            self._repository.finish_logout,
            plan=plan,
            evidence=tuple(evidence),
        )
        return ProviderLogoutResult(
            revoked_session_count=plan.revoked_session_count,
            front_channel_logout_url=front_channel_url,
            provider_failures=sum(item.outcome == "FAILED" for item in evidence),
        )

    async def _cleanup_provider(self, target: LogoutTokenTarget) -> list[CleanupEvidence]:
        evidence: list[CleanupEvidence] = []
        operations: list[tuple[str, Any]] = [
            (
                "provider_logout",
                self._provider.logout_provider_session(refresh_token=target.tokens.refresh_token),
            )
        ]
        if target.tokens.access_token is not None:
            operations.append(
                (
                    "access_revocation",
                    self._provider.revoke_token(
                        token=target.tokens.access_token,
                        token_kind=ProviderTokenKind.ACCESS_TOKEN,
                    ),
                )
            )
        if target.tokens.refresh_token is not None:
            operations.append(
                (
                    "refresh_revocation",
                    self._provider.revoke_token(
                        token=target.tokens.refresh_token,
                        token_kind=ProviderTokenKind.REFRESH_TOKEN,
                    ),
                )
            )
        for operation, awaitable in operations:
            started = perf_counter()
            outcome = "FAILED"
            reason_code: str | None = None
            with self._telemetry.span(
                f"provider.session.{operation}",
                attributes={"provider.session.operation": operation},
            ):
                try:
                    async with asyncio.timeout(self._settings.provider_logout_timeout_seconds):
                        result = await awaitable
                    outcome = result.status.value
                except TimeoutError:
                    reason_code = "PROVIDER_TIMEOUT"
                except ProviderExchangeFailure as failure:
                    reason_code = failure.reason_code
                except Exception:
                    reason_code = "PROVIDER_CLEANUP_INTERNAL_ERROR"
                    LOGGER.exception(
                        "provider cleanup failed unexpectedly",
                        extra={
                            "event": "provider_cleanup_unexpected_failure",
                            "operation": operation,
                        },
                    )
            self._telemetry.record_provider_session(
                operation,
                outcome.casefold(),
                (perf_counter() - started) * 1000,
            )
            evidence.append(
                CleanupEvidence(
                    session_family_id=target.session_family_id,
                    operation=operation,
                    outcome=outcome,
                    reason_code=reason_code,
                )
            )
        return evidence

    async def backchannel_logout(
        self,
        *,
        provider_session: str | None,
        provider_subject: str | None,
        token_identifier: str,
        issued_at: datetime,
        correlation_id: str,
        request_id: str,
    ) -> int:
        with self._telemetry.span(
            "provider.session.backchannel_logout",
            attributes={"provider.session.operation": "backchannel_logout"},
        ):
            return await asyncio.to_thread(
                self._repository.revoke_from_backchannel,
                provider_session=provider_session,
                provider_subject=provider_subject,
                token_identifier=token_identifier,
                issued_at=issued_at,
                correlation_id=correlation_id,
                request_id=request_id,
            )

    @staticmethod
    def _outcome_state(outcome: str) -> str:
        if outcome in {"rotated", "refreshed"}:
            return ProviderSessionState.ACTIVE.value
        if outcome == "retry_scheduled":
            return ProviderSessionState.REFRESH_FAILED.value
        if outcome in {"revoked", "reuse_detected"}:
            return ProviderSessionState.DISPOSED.value
        return ProviderSessionState.REFRESH_REQUIRED.value


class ProviderRefreshWorker:
    """One non-blocking process worker backed by database leases."""

    def __init__(
        self,
        *,
        service: ProviderSessionLifecycleService,
        settings: PortalApiSettings,
        worker_id: str | None = None,
    ) -> None:
        self._service = service
        self._settings = settings
        self._worker_id = worker_id or f"portal-refresh-{uuid4()}"
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(),
            name="portal-provider-refresh",
        )

    async def stop(self) -> None:
        self._stopping.set()
        task = self._task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        self._task = None

    async def run_once(self) -> int:
        return await self._service.refresh_due(worker_id=self._worker_id)

    async def _run(self) -> None:
        LOGGER.info(
            "provider refresh worker started",
            extra={
                "event": "provider_refresh_worker_started",
                "scan_interval_seconds": self._settings.provider_refresh_scan_interval_seconds,
                "batch_size": self._settings.provider_refresh_batch_size,
            },
        )
        try:
            while not self._stopping.is_set():
                try:
                    processed = await self.run_once()
                    if processed:
                        LOGGER.info(
                            "provider refresh batch completed",
                            extra={
                                "event": "provider_refresh_batch_completed",
                                "processed": processed,
                            },
                        )
                except Exception:
                    LOGGER.exception(
                        "provider refresh worker iteration failed",
                        extra={"event": "provider_refresh_worker_iteration_failed"},
                    )
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stopping.wait(),
                        timeout=self._settings.provider_refresh_scan_interval_seconds,
                    )
        finally:
            LOGGER.info(
                "provider refresh worker stopped",
                extra={"event": "provider_refresh_worker_stopped"},
            )
