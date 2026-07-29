"""Model C callback orchestration contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID

from portal_api.auth.provider_config import OidcProviderConfig


class ProviderFailureKind(StrEnum):
    PRE_DISPATCH = "PRE_DISPATCH"
    AUTHORITATIVE_REJECTION = "AUTHORITATIVE_REJECTION"
    AMBIGUOUS = "AMBIGUOUS"


class ProviderExchangeFailure(RuntimeError):
    def __init__(
        self,
        kind: ProviderFailureKind,
        *,
        reason_code: str = "PROVIDER_REQUEST_FAILED",
    ) -> None:
        super().__init__("The configured identity provider exchange failed")
        self.kind = kind
        self.reason_code = reason_code


@dataclass(frozen=True)
class ProviderTokenSet:
    id_token: str
    access_token: str | None
    refresh_token: str | None
    token_type: str
    expires_in: int | None
    refresh_expires_in: int | None = None


@dataclass(frozen=True)
class ProviderRefreshTokenSet:
    access_token: str
    refresh_token: str | None
    id_token: str | None
    token_type: str
    expires_in: int
    refresh_expires_in: int | None = None


class ProviderTokenKind(StrEnum):
    ACCESS_TOKEN = "access_token"
    REFRESH_TOKEN = "refresh_token"


class ProviderOperationStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class ProviderOperationResult:
    status: ProviderOperationStatus


class OidcProviderPort(Protocol):
    async def get_config(self, *, force_refresh: bool = False) -> OidcProviderConfig: ...

    async def exchange_code(
        self,
        *,
        code: str,
        verifier: str,
        redirect_uri: str,
    ) -> ProviderTokenSet: ...

    async def get_jwks(self, *, force_refresh: bool = False) -> dict[str, Any]: ...

    async def refresh_tokens(self, *, refresh_token: str) -> ProviderRefreshTokenSet: ...

    async def revoke_token(
        self,
        *,
        token: str,
        token_kind: ProviderTokenKind,
    ) -> ProviderOperationResult: ...

    async def logout_provider_session(
        self,
        *,
        refresh_token: str | None,
    ) -> ProviderOperationResult: ...

    async def front_channel_logout_url(self) -> str | None: ...


@dataclass(frozen=True)
class ValidatedIdentity:
    issuer: str
    subject: str
    nonce: str
    groups: tuple[str, ...]
    display_name: str | None
    assurance: str
    authenticated_at: datetime
    token_expires_at: datetime
    provider_session: str | None = None


class TokenValidatorPort(Protocol):
    async def validate(
        self,
        *,
        id_token: str,
        expected_nonce_hash: bytes,
    ) -> ValidatedIdentity: ...


@dataclass(frozen=True)
class ResolvedPrincipal:
    principal_id: UUID
    issuer: str
    subject_reference: str
    display_attributes: dict[str, str]
    status: str
    roles: tuple[str, ...]
    environment_ids: tuple[str, ...]
    tenant_id: str
    mapping_revision: str
    assurance: str
    authenticated_at: datetime
    token_expires_at: datetime
    provider_session: str | None = None


class PrincipalResolverPort(Protocol):
    def resolve(self, identity: ValidatedIdentity) -> ResolvedPrincipal: ...


class PolicyOutcome(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INDETERMINATE = "INDETERMINATE"
    ERROR = "ERROR"


@dataclass(frozen=True)
class CallbackPolicyDecision:
    outcome: PolicyOutcome
    reason_code: str
    policy_revision: str
    capability_revision: str


class CallbackPolicyPort(Protocol):
    def evaluate(self, principal: ResolvedPrincipal) -> CallbackPolicyDecision: ...
