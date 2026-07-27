"""Opaque server-session authentication, CSRF, rotation, and revocation."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Connection, Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.csrf import (
    csrf_token_hash,
    csrf_token_matches,
    derive_csrf_token,
)
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.core.config import PortalApiSettings
from portal_api.db.metadata import portal_sessions, portal_token_envelopes
from portal_api.db.unit_of_work import local_transaction


class SessionAuthenticationError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__("The current server session is not authenticated")
        self.reason_code = reason_code


class CsrfValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class AuthenticatedSession:
    session_id: UUID
    session_family_id: UUID
    session_secret: str
    principal_id: UUID
    display_name: str | None
    tenant_id: str
    status: str
    version: int
    roles: tuple[str, ...]
    environment_ids: tuple[str, ...]
    mapping_revision: str
    policy_revision: str
    capability_revision: str
    assurance: str
    authenticated_at: datetime
    idle_expires_at: datetime
    absolute_expires_at: datetime
    identity_verified_until: datetime
    csrf_token_hash: bytes
    csrf_generation: int


@dataclass(frozen=True)
class RotatedSession:
    context: AuthenticatedSession
    session_secret: str


class SessionService:
    def __init__(
        self,
        *,
        engine: Engine,
        settings: PortalApiSettings,
        security_material: EphemeralSecurityMaterial,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._security_material = security_material
        self._audit = audit_ledger or AuditLedger()

    def authenticate(
        self,
        *,
        session_secret: str | None,
        correlation_id: str,
        request_id: str,
    ) -> AuthenticatedSession:
        if not session_secret or len(session_secret) > 512:
            raise SessionAuthenticationError("SESSION_MISSING")
        lookup_hash = self._security_material.protect(
            session_secret,
            purpose=ProtectedPurpose.SESSION,
        )
        now = datetime.now(UTC)
        failure: str | None = None
        row: dict[str, object] | None = None
        with local_transaction(self._engine) as connection:
            durable = (
                connection.execute(
                    select(portal_sessions)
                    .where(portal_sessions.c.session_lookup_hash == lookup_hash)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if durable is None or not hmac.compare_digest(
                durable["session_lookup_hash"],
                lookup_hash,
            ):
                failure = "SESSION_INVALID"
            elif durable["status"] not in {"ACTIVE", "REFRESH_REQUIRED"}:
                failure = f"SESSION_{durable['status']}"
            elif durable["security_epoch"] != self._settings.session_security_epoch:
                failure = "SESSION_SECURITY_EPOCH_INVALID"
                self._transition_session(
                    connection,
                    durable=dict(durable),
                    status="INVALID",
                    reason=failure,
                    now=now,
                    event_type=AuditEventType.SESSION_REVOKED,
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
            elif durable["absolute_expires_at"] <= now:
                failure = "SESSION_EXPIRED_ABSOLUTE"
                self._transition_session(
                    connection,
                    durable=dict(durable),
                    status="EXPIRED_ABSOLUTE",
                    reason=failure,
                    now=now,
                    event_type=AuditEventType.SESSION_EXPIRED_ABSOLUTE,
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
            elif durable["idle_expires_at"] <= now:
                failure = "SESSION_EXPIRED_IDLE"
                self._transition_session(
                    connection,
                    durable=dict(durable),
                    status="EXPIRED_IDLE",
                    reason=failure,
                    now=now,
                    event_type=AuditEventType.SESSION_EXPIRED_IDLE,
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
            elif (
                durable["provider_expires_at"] is not None and durable["provider_expires_at"] <= now
            ) or durable["identity_verified_until"] <= now:
                failure = "SESSION_REFRESH_REQUIRED"
                if durable["status"] == "ACTIVE":
                    connection.execute(
                        update(portal_sessions)
                        .where(
                            portal_sessions.c.session_id == durable["session_id"],
                            portal_sessions.c.version == durable["version"],
                            portal_sessions.c.status == "ACTIVE",
                        )
                        .values(status="REFRESH_REQUIRED")
                    )
            elif durable["status"] == "REFRESH_REQUIRED":
                failure = "SESSION_REFRESH_REQUIRED"
            else:
                interval = timedelta(seconds=self._settings.session_activity_write_interval_seconds)
                last_activity = durable["last_activity_at"]
                if last_activity + interval <= now:
                    idle_expires_at = min(
                        now + timedelta(seconds=self._settings.session_idle_ttl_seconds),
                        durable["absolute_expires_at"],
                    )
                    connection.execute(
                        update(portal_sessions)
                        .where(
                            portal_sessions.c.session_id == durable["session_id"],
                            portal_sessions.c.version == durable["version"],
                            portal_sessions.c.status == "ACTIVE",
                        )
                        .values(
                            last_activity_at=now,
                            idle_expires_at=idle_expires_at,
                        )
                    )
                    row = dict(durable)
                    row["idle_expires_at"] = idle_expires_at
                else:
                    row = dict(durable)
        if failure is not None:
            raise SessionAuthenticationError(failure)
        if row is None:
            raise RuntimeError("Session authentication produced no authoritative outcome")
        return self._context(row=row, session_secret=session_secret)

    def csrf_token(self, session: AuthenticatedSession) -> str:
        token = derive_csrf_token(
            security_material=self._security_material,
            session_secret=session.session_secret,
            generation=session.csrf_generation,
        )
        if not csrf_token_matches(
            security_material=self._security_material,
            token=token,
            expected_hash=session.csrf_token_hash,
        ):
            raise CsrfValidationError("Stored CSRF authority is invalid")
        return token

    def validate_csrf(
        self,
        *,
        session: AuthenticatedSession,
        presented_token: str | None,
        origin: str | None,
        correlation_id: str,
        request_id: str,
    ) -> None:
        origin_valid = origin is not None and any(
            hmac.compare_digest(origin, allowed) for allowed in self._settings.allowed_origin_values
        )
        token_valid = (
            presented_token is not None
            and len(presented_token) <= 256
            and csrf_token_matches(
                security_material=self._security_material,
                token=presented_token,
                expected_hash=session.csrf_token_hash,
            )
        )
        if origin_valid and token_valid:
            return
        with local_transaction(self._engine) as connection:
            self._audit.append(
                connection,
                AuditEvent(
                    event_type=AuditEventType.CSRF_REJECTED,
                    actor_type="PRINCIPAL",
                    principal_id=session.principal_id,
                    session_reference=str(session.session_id),
                    tenant_id=session.tenant_id,
                    action="portal.csrf.validate",
                    reason_code=(
                        "CSRF_ORIGIN_INVALID" if not origin_valid else "CSRF_TOKEN_INVALID"
                    ),
                    correlation_id=correlation_id,
                    request_id=request_id,
                    outcome="DENIED",
                ),
            )
        raise CsrfValidationError("CSRF validation failed")

    def rotate(
        self,
        *,
        session: AuthenticatedSession,
        correlation_id: str,
        request_id: str,
    ) -> RotatedSession:
        now = datetime.now(UTC)
        successor_id = uuid4()
        successor_secret = secrets.token_urlsafe(32)
        lookup_hash = self._security_material.protect(
            successor_secret,
            purpose=ProtectedPurpose.SESSION,
        )
        csrf_generation = session.csrf_generation + 1
        csrf_hash = csrf_token_hash(
            security_material=self._security_material,
            token=derive_csrf_token(
                security_material=self._security_material,
                session_secret=successor_secret,
                generation=csrf_generation,
            ),
        )
        successor: dict[str, object]
        with local_transaction(self._engine) as connection:
            current = (
                connection.execute(
                    select(portal_sessions)
                    .where(portal_sessions.c.session_id == session.session_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                current is None
                or current["status"] != "ACTIVE"
                or current["version"] != session.version
                or current["security_epoch"] != self._settings.session_security_epoch
                or current["absolute_expires_at"] <= now
            ):
                raise SessionAuthenticationError("SESSION_ROTATION_CONFLICT")
            connection.execute(
                update(portal_sessions)
                .where(
                    portal_sessions.c.session_id == current["session_id"],
                    portal_sessions.c.status == "ACTIVE",
                    portal_sessions.c.version == current["version"],
                )
                .values(
                    status="REVOKED",
                    revoked_at=now,
                    revoked_reason="ROTATED",
                    version=current["version"] + 1,
                )
            )
            idle_expires_at = min(
                now + timedelta(seconds=self._settings.session_idle_ttl_seconds),
                current["absolute_expires_at"],
            )
            successor = {
                key: current[key]
                for key in (
                    "session_family_id",
                    "principal_id",
                    "issuer",
                    "subject_reference",
                    "tenant_id",
                    "security_epoch",
                    "roles_snapshot",
                    "environments_snapshot",
                    "mapping_revision",
                    "policy_revision",
                    "capability_revision",
                    "authentication_assurance",
                    "authenticated_at",
                    "absolute_expires_at",
                    "provider_expires_at",
                    "identity_verified_until",
                    "client_signal_classification",
                )
            }
            successor.update(
                {
                    "session_id": successor_id,
                    "session_lookup_hash": lookup_hash,
                    "lookup_key_version": self._security_material.key_version,
                    "predecessor_session_id": current["session_id"],
                    "status": "ACTIVE",
                    "version": 1,
                    "created_at": now,
                    "last_activity_at": now,
                    "idle_expires_at": idle_expires_at,
                    "audit_correlation_id": correlation_id,
                    "csrf_token_hash": csrf_hash,
                    "csrf_generation": csrf_generation,
                }
            )
            connection.execute(insert(portal_sessions).values(**successor))
            self._audit.append(
                connection,
                AuditEvent(
                    event_type=AuditEventType.SESSION_ROTATED,
                    actor_type="PRINCIPAL",
                    principal_id=session.principal_id,
                    session_reference=str(successor_id),
                    tenant_id=session.tenant_id,
                    action="portal.session.refresh",
                    reason_code="SESSION_ROTATED",
                    policy_revision=session.policy_revision,
                    capability_revision=session.capability_revision,
                    authentication_assurance=session.assurance,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    outcome="ROTATED",
                    safe_metadata={
                        "predecessor_session_reference": str(session.session_id),
                    },
                ),
            )
        return RotatedSession(
            context=self._context(row=successor, session_secret=successor_secret),
            session_secret=successor_secret,
        )

    def revoke_current(
        self,
        *,
        session: AuthenticatedSession,
        correlation_id: str,
        request_id: str,
    ) -> int:
        return self._revoke(
            session=session,
            all_for_principal=False,
            correlation_id=correlation_id,
            request_id=request_id,
        )

    def revoke_all(
        self,
        *,
        session: AuthenticatedSession,
        correlation_id: str,
        request_id: str,
    ) -> int:
        return self._revoke(
            session=session,
            all_for_principal=True,
            correlation_id=correlation_id,
            request_id=request_id,
        )

    def _revoke(
        self,
        *,
        session: AuthenticatedSession,
        all_for_principal: bool,
        correlation_id: str,
        request_id: str,
    ) -> int:
        now = datetime.now(UTC)
        revoked = 0
        affected_families: set[UUID] = set()
        with local_transaction(self._engine) as connection:
            query = select(portal_sessions).where(
                portal_sessions.c.principal_id == session.principal_id,
                portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
            )
            if not all_for_principal:
                query = query.where(portal_sessions.c.session_id == session.session_id)
            sessions = (
                connection.execute(query.order_by(portal_sessions.c.created_at).with_for_update())
                .mappings()
                .all()
            )
            self._audit.append(
                connection,
                AuditEvent(
                    event_type=AuditEventType.LOGOUT_REQUESTED,
                    actor_type="PRINCIPAL",
                    principal_id=session.principal_id,
                    session_reference=str(session.session_id),
                    tenant_id=session.tenant_id,
                    action="portal.logout",
                    correlation_id=correlation_id,
                    request_id=request_id,
                    outcome="REQUESTED",
                    safe_metadata={"scope": "all" if all_for_principal else "current"},
                ),
            )
            for durable in sessions:
                result = connection.execute(
                    update(portal_sessions)
                    .where(
                        portal_sessions.c.session_id == durable["session_id"],
                        portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
                        portal_sessions.c.version == durable["version"],
                    )
                    .values(
                        status="REVOKED",
                        revoked_at=now,
                        revoked_reason="LOGOUT_ALL" if all_for_principal else "LOGOUT",
                        version=durable["version"] + 1,
                    )
                )
                revoked += int(result.rowcount or 0)
                affected_families.add(durable["session_family_id"])
            if affected_families:
                connection.execute(
                    delete(portal_token_envelopes).where(
                        portal_token_envelopes.c.session_family_id.in_(affected_families)
                    )
                )
                connection.execute(
                    update(portal_sessions)
                    .where(
                        portal_sessions.c.session_family_id.in_(affected_families),
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
            self._audit.append(
                connection,
                AuditEvent(
                    event_type=AuditEventType.LOGOUT_COMPLETED,
                    actor_type="PRINCIPAL",
                    principal_id=session.principal_id,
                    session_reference=str(session.session_id),
                    tenant_id=session.tenant_id,
                    action="portal.logout",
                    correlation_id=correlation_id,
                    request_id=request_id,
                    outcome="COMPLETED",
                    safe_metadata={"revoked_session_count": revoked},
                ),
            )
        return revoked

    def record_environment_selection(
        self,
        *,
        session: AuthenticatedSession,
        environment_id: str,
        correlation_id: str,
        request_id: str,
    ) -> None:
        with local_transaction(self._engine) as connection:
            self._audit.append(
                connection,
                AuditEvent(
                    event_type=AuditEventType.ENVIRONMENT_SELECTED,
                    actor_type="PRINCIPAL",
                    principal_id=session.principal_id,
                    session_reference=str(session.session_id),
                    tenant_id=session.tenant_id,
                    environment_id=environment_id,
                    action="portal.environment.select",
                    decision="ALLOW",
                    policy_revision=session.policy_revision,
                    capability_revision=session.capability_revision,
                    authentication_assurance=session.assurance,
                    correlation_id=correlation_id,
                    request_id=request_id,
                    outcome="SELECTED",
                ),
            )

    def _transition_session(
        self,
        connection: Connection,
        *,
        durable: Mapping[str, Any],
        status: str,
        reason: str,
        now: datetime,
        event_type: AuditEventType,
        correlation_id: str,
        request_id: str,
    ) -> None:
        result = connection.execute(
            update(portal_sessions)
            .where(
                portal_sessions.c.session_id == durable["session_id"],
                portal_sessions.c.status.in_(("ACTIVE", "REFRESH_REQUIRED")),
                portal_sessions.c.version == durable["version"],
            )
            .values(
                status=status,
                revoked_at=now,
                revoked_reason=reason,
                version=durable["version"] + 1,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("Session terminal transition lost its authority")
        self._audit.append(
            connection,
            AuditEvent(
                event_type=event_type,
                actor_type="PRINCIPAL",
                principal_id=durable["principal_id"],
                session_reference=str(durable["session_id"]),
                tenant_id=durable["tenant_id"],
                action="portal.session.authenticate",
                reason_code=reason,
                correlation_id=correlation_id,
                request_id=request_id,
                outcome=status,
            ),
        )

    @staticmethod
    def _context(
        *,
        row: Mapping[str, Any],
        session_secret: str,
    ) -> AuthenticatedSession:
        display_name = None
        return AuthenticatedSession(
            session_id=row["session_id"],
            session_family_id=row["session_family_id"],
            session_secret=session_secret,
            principal_id=row["principal_id"],
            display_name=display_name,
            tenant_id=str(row["tenant_id"]),
            status=str(row["status"]),
            version=int(row["version"]),
            roles=tuple(row["roles_snapshot"]),
            environment_ids=tuple(row["environments_snapshot"]),
            mapping_revision=str(row["mapping_revision"]),
            policy_revision=str(row["policy_revision"]),
            capability_revision=str(row["capability_revision"]),
            assurance=str(row["authentication_assurance"]),
            authenticated_at=row["authenticated_at"],
            idle_expires_at=row["idle_expires_at"],
            absolute_expires_at=row["absolute_expires_at"],
            identity_verified_until=row["identity_verified_until"],
            csrf_token_hash=row["csrf_token_hash"],
            csrf_generation=int(row["csrf_generation"]),
        )
