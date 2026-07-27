"""Authoritative Model C OIDC callback orchestration."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Connection, Engine
from starlette.concurrency import run_in_threadpool

from portal_api.audit.ledger import AuditLedger
from portal_api.audit.models import AuditEvent, AuditEventType
from portal_api.auth.ports import (
    CallbackPolicyPort,
    OidcProviderPort,
    PolicyOutcome,
    PrincipalResolverPort,
    ProviderExchangeFailure,
    ProviderFailureKind,
    TokenValidatorPort,
)
from portal_api.auth.protected_value import ProtectedValue, ProtectedValueCipher
from portal_api.auth.provider_config import OidcProviderConfig
from portal_api.auth.security_material import EphemeralSecurityMaterial, ProtectedPurpose
from portal_api.auth.session_store import (
    CallbackSession,
    CallbackSessionStore,
    FinalizationRejected,
)
from portal_api.core.config import PortalApiSettings
from portal_api.db.metadata import oidc_login_transactions
from portal_api.db.unit_of_work import local_transaction

MAX_CODE_LENGTH = 4096
MAX_STATE_LENGTH = 512
MAX_PROVIDER_ERROR_LENGTH = 128


class CallbackFailure(RuntimeError):
    def __init__(
        self,
        reason_code: str,
        *,
        status_code: int = 401,
        retryable: bool = False,
    ) -> None:
        super().__init__("OIDC callback failed")
        self.reason_code = reason_code
        self.status_code = status_code
        self.retryable = retryable


@dataclass(frozen=True)
class CallbackCommand:
    state: str
    code: str | None
    provider_error: str | None
    browser_binding: str | None
    provider_issuer_hint: str | None = None


@dataclass(frozen=True)
class ClaimedLoginTransaction:
    transaction_id: UUID
    claim_id: UUID
    expected_nonce_hash: bytes
    encrypted_verifier: dict[str, object]
    redirect_uri: str


class CallbackOrchestrator:
    """Single owner of claim, external exchange, evaluation, and atomic finalization."""

    def __init__(
        self,
        *,
        engine: Engine,
        settings: PortalApiSettings,
        security_material: EphemeralSecurityMaterial,
        protected_value_cipher: ProtectedValueCipher,
        provider: OidcProviderPort,
        token_validator: TokenValidatorPort,
        principal_resolver: PrincipalResolverPort,
        policy: CallbackPolicyPort,
        session_store: CallbackSessionStore,
        audit_ledger: AuditLedger | None = None,
    ) -> None:
        self._engine = engine
        self._settings = settings
        self._security_material = security_material
        self._protected_value_cipher = protected_value_cipher
        self._provider = provider
        self._token_validator = token_validator
        self._principal_resolver = principal_resolver
        self._policy = policy
        self._session_store = session_store
        self._audit = audit_ledger or AuditLedger()
        self._provider_config = OidcProviderConfig.from_settings(settings)

    async def process(
        self,
        command: CallbackCommand,
        *,
        correlation_id: str,
        request_id: str,
    ) -> CallbackSession:
        self._validate_transport(command)
        claim = await run_in_threadpool(
            self._claim,
            command,
            correlation_id,
            request_id,
        )
        if command.provider_error is not None:
            await self._dispose_claim(
                claim,
                terminal_state="INVALIDATED",
                reason_code="OIDC_PROVIDER_ERROR_PRE_DISPATCH",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise CallbackFailure("OIDC_PROVIDER_ERROR_PRE_DISPATCH")
        if command.code is None:
            await self._dispose_claim(
                claim,
                terminal_state="INVALIDATED",
                reason_code="OIDC_CODE_MISSING",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise CallbackFailure("OIDC_CODE_MISSING")

        try:
            verifier = self._decrypt_verifier(claim)
            token_set = await self._provider.exchange_code(
                code=command.code,
                verifier=verifier,
                redirect_uri=claim.redirect_uri,
            )
            identity = await self._token_validator.validate(
                id_token=token_set.id_token,
                expected_nonce_hash=claim.expected_nonce_hash,
            )
            principal = await run_in_threadpool(self._principal_resolver.resolve, identity)
            decision = await run_in_threadpool(self._policy.evaluate, principal)
            if decision.outcome is not PolicyOutcome.ALLOW:
                await self._dispose_claim(
                    claim,
                    terminal_state="CONSUMED",
                    reason_code=f"CALLBACK_POLICY_{decision.outcome.value}",
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
                raise CallbackFailure("CALLBACK_AUTHORIZATION_FAILED", status_code=403)
            session = await run_in_threadpool(
                self._session_store.finalize,
                transaction_id=claim.transaction_id,
                claim_id=claim.claim_id,
                principal=principal,
                decision=decision,
                token_set=token_set,
                correlation_id=correlation_id,
                request_id=request_id,
            )
        except ProviderExchangeFailure as error:
            terminal_state = (
                "INVALIDATED" if error.kind is ProviderFailureKind.PRE_DISPATCH else "CONSUMED"
            )
            await self._dispose_claim(
                claim,
                terminal_state=terminal_state,
                reason_code=f"OIDC_PROVIDER_{error.kind.value}",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise CallbackFailure(
                "OIDC_PROVIDER_FAILURE",
                status_code=503,
                retryable=False,
            ) from error
        except CallbackFailure:
            raise
        except FinalizationRejected as error:
            await self._dispose_claim(
                claim,
                terminal_state="CONSUMED",
                reason_code="CALLBACK_FINALIZATION_CONFLICT",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise CallbackFailure("CALLBACK_FINALIZATION_CONFLICT") from error
        except (ValueError, TypeError) as error:
            await self._dispose_claim(
                claim,
                terminal_state="CONSUMED",
                reason_code="CALLBACK_IDENTITY_INVALID",
                correlation_id=correlation_id,
                request_id=request_id,
            )
            raise CallbackFailure("CALLBACK_IDENTITY_INVALID") from error
        except Exception as error:
            status = await run_in_threadpool(
                self._authoritative_status,
                claim.transaction_id,
            )
            if status == "CLAIMED":
                await self._dispose_claim(
                    claim,
                    terminal_state="CONSUMED",
                    reason_code="CALLBACK_PROCESSING_ERROR",
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
            raise CallbackFailure(
                "CALLBACK_PROCESSING_ERROR",
                status_code=503,
                retryable=False,
            ) from error
        finally:
            if "verifier" in locals():
                verifier = ""
        return session

    def _validate_transport(self, command: CallbackCommand) -> None:
        if not command.state or len(command.state) > MAX_STATE_LENGTH:
            raise CallbackFailure("OIDC_STATE_INVALID")
        if command.code is not None and (not command.code or len(command.code) > MAX_CODE_LENGTH):
            raise CallbackFailure("OIDC_CODE_INVALID")
        if command.provider_error is not None and (
            not command.provider_error or len(command.provider_error) > MAX_PROVIDER_ERROR_LENGTH
        ):
            raise CallbackFailure("OIDC_PROVIDER_RESPONSE_INVALID")
        if (command.code is None) == (command.provider_error is None):
            raise CallbackFailure("OIDC_PROVIDER_RESPONSE_INVALID")
        if (
            command.provider_issuer_hint is not None
            and command.provider_issuer_hint != self._provider_config.issuer
        ):
            raise CallbackFailure("OIDC_PROVIDER_RESPONSE_INVALID")

    def _claim(
        self,
        command: CallbackCommand,
        correlation_id: str,
        request_id: str,
    ) -> ClaimedLoginTransaction:
        state_hash = self._security_material.protect(
            command.state,
            purpose=ProtectedPurpose.OIDC_STATE,
        )
        binding_hash = (
            self._security_material.protect(
                command.browser_binding,
                purpose=ProtectedPurpose.BROWSER_BINDING,
            )
            if command.browser_binding is not None
            else None
        )
        now = datetime.now(UTC)
        failure: str | None = None
        claim: ClaimedLoginTransaction | None = None
        with local_transaction(self._engine) as connection:
            transaction = (
                connection.execute(
                    select(oidc_login_transactions)
                    .where(oidc_login_transactions.c.state_hash == state_hash)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if transaction is None or not hmac.compare_digest(
                transaction["state_hash"], state_hash
            ):
                failure = "OIDC_STATE_INVALID"
            elif transaction["status"] != "PENDING":
                failure = f"OIDC_TRANSACTION_{transaction['status']}"
            elif transaction["expires_at"] <= now:
                failure = "OIDC_TRANSACTION_EXPIRED"
                self._terminal_update(
                    connection,
                    transaction_id=transaction["transaction_id"],
                    from_status="PENDING",
                    to_status="EXPIRED",
                    now=now,
                    version=transaction["version"],
                )
            elif binding_hash is None or not hmac.compare_digest(
                transaction["browser_binding_hash"],
                binding_hash,
            ):
                failure = "OIDC_BROWSER_BINDING_INVALID"
                self._terminal_update(
                    connection,
                    transaction_id=transaction["transaction_id"],
                    from_status="PENDING",
                    to_status="INVALIDATED",
                    now=now,
                    version=transaction["version"],
                )
            elif (
                transaction["provider_id"] != self._provider_config.provider_id
                or transaction["redirect_uri"] != self._provider_config.redirect_uri
            ):
                failure = "OIDC_PROVIDER_BINDING_INVALID"
                self._terminal_update(
                    connection,
                    transaction_id=transaction["transaction_id"],
                    from_status="PENDING",
                    to_status="INVALIDATED",
                    now=now,
                    version=transaction["version"],
                )
            else:
                claim_id = uuid4()
                claimed = connection.execute(
                    update(oidc_login_transactions)
                    .where(
                        oidc_login_transactions.c.transaction_id == transaction["transaction_id"],
                        oidc_login_transactions.c.status == "PENDING",
                        oidc_login_transactions.c.version == transaction["version"],
                    )
                    .values(
                        status="CLAIMED",
                        claimed_by=claim_id,
                        claimed_at=now,
                        updated_at=now,
                        version=transaction["version"] + 1,
                    )
                )
                if claimed.rowcount != 1:
                    raise RuntimeError("Callback claim lost its authoritative transition")
                self._append_audit(
                    connection,
                    event_type=AuditEventType.LOGIN_STARTED,
                    outcome="CLAIMED",
                    reason_code=None,
                    transaction_id=transaction["transaction_id"],
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
                claim = ClaimedLoginTransaction(
                    transaction_id=transaction["transaction_id"],
                    claim_id=claim_id,
                    expected_nonce_hash=transaction["nonce_hash"],
                    encrypted_verifier=dict(transaction["pkce_verifier_encrypted"]),
                    redirect_uri=str(transaction["redirect_uri"]),
                )
            if failure is not None:
                self._append_audit(
                    connection,
                    event_type=AuditEventType.LOGIN_FAILED,
                    outcome="DENIED",
                    reason_code=failure,
                    transaction_id=(
                        transaction["transaction_id"] if transaction is not None else None
                    ),
                    correlation_id=correlation_id,
                    request_id=request_id,
                )
        if failure is not None:
            raise CallbackFailure(failure)
        if claim is None:
            raise RuntimeError("Callback claim produced no authoritative outcome")
        return claim

    async def _dispose_claim(
        self,
        claim: ClaimedLoginTransaction,
        *,
        terminal_state: str,
        reason_code: str,
        correlation_id: str,
        request_id: str,
    ) -> None:
        await run_in_threadpool(
            self._terminalize_claim,
            claim,
            terminal_state,
            reason_code,
            correlation_id,
            request_id,
        )

    def _terminalize_claim(
        self,
        claim: ClaimedLoginTransaction,
        terminal_state: str,
        reason_code: str,
        correlation_id: str,
        request_id: str,
    ) -> None:
        now = datetime.now(UTC)
        with local_transaction(self._engine) as connection:
            transaction = (
                connection.execute(
                    select(oidc_login_transactions)
                    .where(oidc_login_transactions.c.transaction_id == claim.transaction_id)
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if (
                transaction is None
                or transaction["status"] != "CLAIMED"
                or transaction["claimed_by"] != claim.claim_id
            ):
                raise RuntimeError("Claim cannot be terminalized authoritatively")
            self._terminal_update(
                connection,
                transaction_id=claim.transaction_id,
                from_status="CLAIMED",
                to_status=terminal_state,
                now=now,
                version=transaction["version"],
            )
            self._append_audit(
                connection,
                event_type=AuditEventType.LOGIN_FAILED,
                outcome="DENIED",
                reason_code=reason_code,
                transaction_id=claim.transaction_id,
                correlation_id=correlation_id,
                request_id=request_id,
            )

    @staticmethod
    def _terminal_update(
        connection: Connection,
        *,
        transaction_id: UUID,
        from_status: str,
        to_status: str,
        now: datetime,
        version: int,
    ) -> None:
        result = connection.execute(
            update(oidc_login_transactions)
            .where(
                oidc_login_transactions.c.transaction_id == transaction_id,
                oidc_login_transactions.c.status == from_status,
                oidc_login_transactions.c.version == version,
            )
            .values(
                status=to_status,
                consumed_at=now if to_status == "CONSUMED" else None,
                updated_at=now,
                version=version + 1,
            )
        )
        if result.rowcount != 1:
            raise RuntimeError("Terminal callback transition was not authoritative")

    def _decrypt_verifier(self, claim: ClaimedLoginTransaction) -> str:
        protected = ProtectedValue.deserialize(json.dumps(claim.encrypted_verifier))
        plaintext = self._protected_value_cipher.decrypt(
            protected,
            context=str(claim.transaction_id).encode("ascii"),
        )
        try:
            return plaintext.decode("ascii")
        except UnicodeDecodeError as error:
            raise CallbackFailure("OIDC_PKCE_VERIFIER_INVALID") from error

    def _authoritative_status(self, transaction_id: UUID) -> str | None:
        with self._engine.connect() as connection:
            status = connection.execute(
                select(oidc_login_transactions.c.status).where(
                    oidc_login_transactions.c.transaction_id == transaction_id
                )
            ).scalar_one_or_none()
        return str(status) if status is not None else None

    def _append_audit(
        self,
        connection: Connection,
        *,
        event_type: AuditEventType,
        outcome: str,
        reason_code: str | None,
        transaction_id: UUID | None,
        correlation_id: str,
        request_id: str,
    ) -> None:
        metadata = (
            {"transaction_reference": str(transaction_id)} if transaction_id is not None else {}
        )
        self._audit.append(
            connection,
            AuditEvent(
                event_type=event_type,
                actor_type="ANONYMOUS",
                action="portal.authenticate",
                reason_code=reason_code,
                correlation_id=correlation_id,
                request_id=request_id,
                outcome=outcome,
                safe_metadata=metadata,
            ),
        )
