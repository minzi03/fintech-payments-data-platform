"""Durable login-intent lifecycle and atomic login initiation."""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from sqlalchemy import insert, select, update
from sqlalchemy.engine import Connection, Engine

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.login_transaction import (
    PendingLoginTransaction,
    build_pending_login_transaction,
)
from portal_api.auth.ports import OidcProviderPort
from portal_api.auth.protected_value import ProtectedValueCipher
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.core.config import PortalApiSettings
from portal_api.db.metadata import oidc_login_transactions, portal_login_intents
from portal_api.db.unit_of_work import local_transaction


class LoginInitiationError(ValueError):
    """Safe login-initiation rejection with an internal audit reason."""

    def __init__(self, reason_code: str, *, origin_failure: bool = False) -> None:
        super().__init__("Login initiation was rejected")
        self.reason_code = reason_code
        self.origin_failure = origin_failure


@dataclass(frozen=True)
class LoginContext:
    intent_token: str
    selected_provider: str
    return_to: str | None
    expires_at: datetime


@dataclass(frozen=True)
class LoginRedirect:
    location: str
    browser_binding_secret: str


class LoginInitiationService:
    """Own login context and the intent-to-transaction atomic boundary."""

    def __init__(
        self,
        *,
        engine: Engine,
        settings: PortalApiSettings,
        security_material: EphemeralSecurityMaterial,
        protected_value_cipher: ProtectedValueCipher,
        provider: OidcProviderPort,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._security_material = security_material
        self._protected_value_cipher = protected_value_cipher
        self._audit = audit_ledger or AuditLedger()
        self._provider = provider

    def create_context(
        self,
        *,
        requested_return_path: str | None,
        correlation_id: str,
        request_id: str,
    ) -> LoginContext:
        try:
            return_path = self._normalize_return_path(requested_return_path)
        except LoginInitiationError as error:
            self._record_pre_transaction_failure(
                reason_code=error.reason_code,
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise
        issued_at = datetime.now(UTC)
        expires_at = issued_at + timedelta(seconds=self._settings.login_intent_ttl_seconds)
        intent_token = secrets.token_urlsafe(32)
        intent_id = uuid4()
        intent_hash = self._security_material.protect(
            intent_token,
            purpose=ProtectedPurpose.LOGIN_INTENT,
        )
        with local_transaction(self._engine) as connection:
            connection.execute(
                insert(portal_login_intents).values(
                    intent_id=intent_id,
                    intent_lookup_hash=intent_hash,
                    selected_provider=self._settings.oidc_provider_id,
                    validated_return_path=return_path,
                    status="PENDING",
                    version=1,
                    created_at=issued_at,
                    expires_at=expires_at,
                )
            )
            self._append_intent_audit(
                connection,
                event_type=AuditEventType.LOGIN_STARTED,
                outcome="CREATED",
                reason_code=None,
                intent_event="created",
                correlation_id=correlation_id,
                request_id=request_id,
                safe_metadata={"selected_provider": self._settings.oidc_provider_id},
            )
        return LoginContext(
            intent_token=intent_token,
            selected_provider=self._settings.oidc_provider_id,
            return_to=return_path,
            expires_at=expires_at,
        )

    async def start_login(
        self,
        *,
        intent_token: str | None,
        requested_return_path: str | None,
        extra_fields: frozenset[str],
        origin: str | None,
        referer: str | None,
        correlation_id: str,
        request_id: str,
    ) -> LoginRedirect:
        if extra_fields:
            self._record_pre_transaction_failure(
                reason_code="LOGIN_INTENT_FORBIDDEN_FIELD",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise LoginInitiationError("LOGIN_INTENT_FORBIDDEN_FIELD")
        if not self._origin_is_allowed(origin=origin, referer=referer):
            self._record_pre_transaction_failure(
                reason_code="LOGIN_ORIGIN_INVALID",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise LoginInitiationError("LOGIN_ORIGIN_INVALID", origin_failure=True)
        if not intent_token:
            self._record_pre_transaction_failure(
                reason_code="LOGIN_INTENT_MISSING",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise LoginInitiationError("LOGIN_INTENT_MISSING")

        provider = await self._provider.get_config()
        now = datetime.now(UTC)
        intent_hashes = tuple(
            protected
            for _, protected in self._security_material.protect_candidates(
                intent_token,
                purpose=ProtectedPurpose.LOGIN_INTENT,
                at=now,
            )
        )
        redirect: LoginRedirect | None = None
        failure: str | None = None
        with local_transaction(self._engine) as connection:
            intent = (
                connection.execute(
                    select(portal_login_intents)
                    .where(portal_login_intents.c.intent_lookup_hash.in_(intent_hashes))
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if intent is None or not any(
                hmac.compare_digest(intent["intent_lookup_hash"], candidate)
                for candidate in intent_hashes
            ):
                failure = "LOGIN_INTENT_INVALID"
            elif intent["status"] == "CONSUMED":
                failure = "LOGIN_INTENT_REPLAYED"
            elif intent["status"] == "EXPIRED" or intent["expires_at"] <= now:
                failure = "LOGIN_INTENT_EXPIRED"
                if intent["status"] == "PENDING":
                    connection.execute(
                        update(portal_login_intents)
                        .where(portal_login_intents.c.intent_id == intent["intent_id"])
                        .values(status="EXPIRED", version=intent["version"] + 1)
                    )
            else:
                try:
                    return_path = self._validated_bound_return_path(
                        stored=intent["validated_return_path"],
                        requested=requested_return_path,
                    )
                except LoginInitiationError as error:
                    failure = error.reason_code

            if failure is None and intent is not None:
                transaction = build_pending_login_transaction(
                    settings=self._settings,
                    provider=provider,
                    security_material=self._security_material,
                    protected_value_cipher=self._protected_value_cipher,
                    return_path=return_path,
                    now=now,
                )
                consumed = connection.execute(
                    update(portal_login_intents)
                    .where(
                        portal_login_intents.c.intent_id == intent["intent_id"],
                        portal_login_intents.c.status == "PENDING",
                        portal_login_intents.c.version == intent["version"],
                    )
                    .values(
                        status="CONSUMED",
                        consumed_at=now,
                        version=intent["version"] + 1,
                    )
                )
                if consumed.rowcount != 1:
                    raise RuntimeError("Login intent one-use transition was not authoritative")
                connection.execute(insert(oidc_login_transactions).values(**transaction.values))
                self._append_intent_audit(
                    connection,
                    event_type=AuditEventType.LOGIN_STARTED,
                    outcome="VALIDATED",
                    reason_code=None,
                    intent_event="validated",
                    correlation_id=correlation_id,
                    request_id=request_id,
                    safe_metadata={"return_path": return_path},
                )
                self._append_intent_audit(
                    connection,
                    event_type=AuditEventType.LOGIN_STARTED,
                    outcome="CONSUMED",
                    reason_code=None,
                    intent_event="consumed",
                    correlation_id=correlation_id,
                    request_id=request_id,
                    safe_metadata={
                        "transaction_reference": str(transaction.transaction_id),
                    },
                )
                redirect = LoginRedirect(
                    location=self._authorization_url(provider, transaction),
                    browser_binding_secret=transaction.browser_binding_secret,
                )

            if failure is not None:
                self._append_intent_audit(
                    connection,
                    event_type=AuditEventType.LOGIN_FAILED,
                    outcome="DENIED",
                    reason_code=failure,
                    intent_event="rejected",
                    correlation_id=correlation_id,
                    request_id=request_id,
                    safe_metadata={},
                )

        if failure is not None:
            raise LoginInitiationError(failure)
        if redirect is None:
            raise RuntimeError("Login initiation produced no authoritative outcome")
        return redirect

    def _record_pre_transaction_failure(
        self,
        *,
        reason_code: str,
        correlation_id: str,
        request_id: str,
    ) -> None:
        with local_transaction(self._engine) as connection:
            self._append_intent_audit(
                connection,
                event_type=AuditEventType.LOGIN_FAILED,
                outcome="DENIED",
                reason_code=reason_code,
                intent_event="rejected",
                correlation_id=correlation_id,
                request_id=request_id,
                safe_metadata={},
            )

    def _append_intent_audit(
        self,
        connection: Connection,
        *,
        event_type: AuditEventType,
        outcome: str,
        reason_code: str | None,
        intent_event: str,
        correlation_id: str,
        request_id: str,
        safe_metadata: dict[str, object],
    ) -> None:
        self._audit.append(
            connection,
            AuditEvent(
                event_type=event_type,
                actor_type="ANONYMOUS",
                outcome=outcome,
                correlation_id=correlation_id,
                request_id=request_id,
                reason_code=reason_code,
                action="portal.authenticate",
                safe_metadata={
                    "login_intent_event": intent_event,
                    **safe_metadata,
                },
            ),
        )

    def _normalize_return_path(self, requested: str | None) -> str | None:
        if requested is None:
            return None
        parsed = urlsplit(requested)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or parsed.query
            or requested not in self._settings.allowed_return_path_values
        ):
            raise LoginInitiationError("LOGIN_RETURN_PATH_INVALID")
        return parsed.path

    def _validated_bound_return_path(self, *, stored: str | None, requested: str | None) -> str:
        normalized = self._normalize_return_path(requested)
        if normalized != stored:
            raise LoginInitiationError("LOGIN_RETURN_PATH_MISMATCH")
        return stored or "/"

    def _origin_is_allowed(self, *, origin: str | None, referer: str | None) -> bool:
        candidate = origin
        if candidate is None and referer is not None:
            parsed = urlsplit(referer)
            if parsed.scheme and parsed.netloc:
                candidate = f"{parsed.scheme}://{parsed.netloc}"
        if candidate is None:
            return False
        return any(
            hmac.compare_digest(candidate, allowed)
            for allowed in self._settings.allowed_origin_values
        )

    def _authorization_url(
        self,
        provider: OidcProviderConfig,
        transaction: PendingLoginTransaction,
    ) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": provider.client_id,
                "redirect_uri": provider.redirect_uri,
                "scope": " ".join(provider.scopes),
                "state": transaction.state,
                "nonce": transaction.nonce,
                "code_challenge": transaction.code_challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"{provider.authorization_endpoint}?{query}"
