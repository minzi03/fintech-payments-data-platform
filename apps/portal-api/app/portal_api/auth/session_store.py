"""Atomic callback finalization and opaque server-session persistence."""

from __future__ import annotations

import base64
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.engine import Connection, Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.ports import (
    CallbackPolicyDecision,
    PolicyOutcome,
    ProviderTokenSet,
    ResolvedPrincipal,
)
from portal_api.auth.protected_value import ProtectedValueCipher
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.core.config import PortalApiSettings
from portal_api.db.metadata import (
    oidc_login_transactions,
    portal_principals,
    portal_sessions,
    portal_token_envelopes,
)
from portal_api.db.unit_of_work import local_transaction

AUTH_TAG_BYTES = 16


class FinalizationRejected(RuntimeError):
    """Authoritative final-state conflict."""


@dataclass(frozen=True)
class CallbackSession:
    session_secret: str
    session_id: UUID
    return_path: str
    absolute_expires_at: datetime


class CallbackSessionStore:
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
        self._protected_value_cipher = protected_value_cipher
        self._audit = audit_ledger or AuditLedger()

    def finalize(
        self,
        *,
        transaction_id: UUID,
        claim_id: UUID,
        principal: ResolvedPrincipal,
        decision: CallbackPolicyDecision,
        token_set: ProviderTokenSet,
        correlation_id: str,
        request_id: str,
    ) -> CallbackSession:
        if decision.outcome is not PolicyOutcome.ALLOW:
            raise FinalizationRejected("Only ALLOW may finalize a callback")
        now = datetime.now(UTC)
        session_id = uuid4()
        session_family_id = uuid4()
        session_secret = secrets.token_urlsafe(32)
        session_lookup_hash = self._security_material.protect(
            session_secret,
            purpose=ProtectedPurpose.SESSION,
        )
        absolute_expires_at = now + timedelta(seconds=self._settings.session_absolute_ttl_seconds)
        idle_expires_at = min(
            now + timedelta(seconds=self._settings.session_idle_ttl_seconds),
            absolute_expires_at,
        )
        identity_verified_until = min(
            now + timedelta(seconds=self._settings.identity_freshness_seconds),
            principal.token_expires_at,
            absolute_expires_at,
        )

        with local_transaction(self._engine) as connection:
            transaction = (
                connection.execute(
                    select(oidc_login_transactions)
                    .where(oidc_login_transactions.c.transaction_id == transaction_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                transaction is None
                or transaction["status"] != "CLAIMED"
                or transaction["claimed_by"] != claim_id
                or transaction["expires_at"] <= now
            ):
                raise FinalizationRejected("Claim is no longer eligible for finalization")

            connection.execute(
                postgresql_insert(portal_principals)
                .values(
                    principal_id=principal.principal_id,
                    issuer=principal.issuer,
                    subject_reference=principal.subject_reference,
                    display_attributes=principal.display_attributes,
                    status="ACTIVE",
                    created_at=now,
                    updated_at=now,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        portal_principals.c.issuer,
                        portal_principals.c.subject_reference,
                    ]
                )
            )
            durable_principal = (
                connection.execute(
                    select(portal_principals)
                    .where(
                        portal_principals.c.issuer == principal.issuer,
                        portal_principals.c.subject_reference == principal.subject_reference,
                    )
                    .with_for_update()
                )
                .mappings()
                .one()
            )
            if (
                durable_principal["principal_id"] != principal.principal_id
                or durable_principal["status"] != "ACTIVE"
            ):
                raise FinalizationRejected("Principal authority changed before finalization")
            connection.execute(
                update(portal_principals)
                .where(portal_principals.c.principal_id == principal.principal_id)
                .values(
                    display_attributes=principal.display_attributes,
                    updated_at=now,
                )
            )

            connection.execute(
                insert(portal_sessions).values(
                    session_id=session_id,
                    session_lookup_hash=session_lookup_hash,
                    lookup_key_version=self._security_material.key_version,
                    session_family_id=session_family_id,
                    predecessor_session_id=None,
                    principal_id=principal.principal_id,
                    issuer=principal.issuer,
                    subject_reference=principal.subject_reference,
                    tenant_id=principal.tenant_id,
                    status="ACTIVE",
                    version=1,
                    security_epoch=self._settings.session_security_epoch,
                    roles_snapshot=list(principal.roles),
                    environments_snapshot=list(principal.environment_ids),
                    mapping_revision=principal.mapping_revision,
                    policy_revision=decision.policy_revision,
                    capability_revision=decision.capability_revision,
                    authentication_assurance=principal.assurance,
                    created_at=now,
                    authenticated_at=principal.authenticated_at,
                    last_activity_at=now,
                    idle_expires_at=idle_expires_at,
                    absolute_expires_at=absolute_expires_at,
                    provider_expires_at=principal.token_expires_at,
                    identity_verified_until=identity_verified_until,
                    client_signal_classification={},
                    audit_correlation_id=correlation_id,
                )
            )
            self._persist_required_tokens(
                connection=connection,
                session_family_id=session_family_id,
                token_set=token_set,
                now=now,
            )
            consumed = connection.execute(
                update(oidc_login_transactions)
                .where(
                    oidc_login_transactions.c.transaction_id == transaction_id,
                    oidc_login_transactions.c.status == "CLAIMED",
                    oidc_login_transactions.c.claimed_by == claim_id,
                    oidc_login_transactions.c.version == transaction["version"],
                )
                .values(
                    status="CONSUMED",
                    consumed_at=now,
                    updated_at=now,
                    version=transaction["version"] + 1,
                )
            )
            if consumed.rowcount != 1:
                raise FinalizationRejected("Login transaction consumption lost its claim")
            for event_type, outcome in (
                (AuditEventType.LOGIN_SUCCEEDED, "SUCCEEDED"),
                (AuditEventType.SESSION_CREATED, "CREATED"),
            ):
                self._audit.append(
                    connection,
                    AuditEvent(
                        event_type=event_type,
                        actor_type="PRINCIPAL",
                        principal_id=principal.principal_id,
                        session_reference=str(session_id),
                        tenant_id=principal.tenant_id,
                        action="portal.authenticate",
                        decision=decision.outcome.value,
                        reason_code=decision.reason_code,
                        policy_revision=decision.policy_revision,
                        capability_revision=decision.capability_revision,
                        authentication_assurance=principal.assurance,
                        correlation_id=correlation_id,
                        request_id=request_id,
                        outcome=outcome,
                        safe_metadata={
                            "transaction_reference": str(transaction_id),
                        },
                    ),
                )
        return CallbackSession(
            session_secret=session_secret,
            session_id=session_id,
            return_path=str(transaction["return_path"]),
            absolute_expires_at=absolute_expires_at,
        )

    def _persist_required_tokens(
        self,
        *,
        connection: Connection,
        session_family_id: UUID,
        token_set: ProviderTokenSet,
        now: datetime,
    ) -> None:
        retained = {
            key: value
            for key, value in {
                "access_token": token_set.access_token,
                "refresh_token": token_set.refresh_token,
            }.items()
            if value is not None
        }
        if not retained:
            return
        protected = self._protected_value_cipher.encrypt(
            json.dumps(retained, separators=(",", ":")).encode("utf-8"),
            context=str(session_family_id).encode("ascii"),
        )
        combined_ciphertext = base64.urlsafe_b64decode(protected.ciphertext.encode("ascii"))
        if len(combined_ciphertext) <= AUTH_TAG_BYTES:
            raise RuntimeError("Protected provider token envelope is invalid")
        connection.execute(
            insert(portal_token_envelopes).values(
                envelope_id=uuid4(),
                session_family_id=session_family_id,
                ciphertext=combined_ciphertext[:-AUTH_TAG_BYTES],
                nonce=base64.urlsafe_b64decode(protected.nonce.encode("ascii")),
                authentication_tag=combined_ciphertext[-AUTH_TAG_BYTES:],
                wrapped_data_key=base64.urlsafe_b64decode(
                    protected.wrapped_data_key.encode("ascii")
                ),
                wrapped_data_key_nonce=base64.urlsafe_b64decode(
                    protected.wrapped_data_key_nonce.encode("ascii")
                ),
                kms_key_id=protected.key_reference,
                token_generation=1,
                created_at=now,
            )
        )
