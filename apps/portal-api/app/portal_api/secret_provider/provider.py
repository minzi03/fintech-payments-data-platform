"""Vendor-neutral synchronous secret-provider contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from portal_api.secret_provider.models import ResolvedSecret, SecretReference


class SecretProviderFailure(StrEnum):
    UNAVAILABLE = "unavailable"
    NOT_STARTED = "not-started"
    NOT_FOUND = "not-found"
    VERSION_MISMATCH = "version-mismatch"
    INVALID_VALUE = "invalid-value"
    PROVIDER_MISMATCH = "provider-mismatch"
    PRODUCTION_RESTRICTED = "production-restricted"
    CONTINUITY_FAILURE = "continuity-failure"


class SecretProviderError(RuntimeError):
    """Deterministic failure that reports only safe provider/reference metadata."""

    def __init__(
        self,
        failure: SecretProviderFailure,
        *,
        provider_id: str,
        reference: SecretReference | None = None,
    ) -> None:
        self.failure = failure
        self.provider_id = provider_id
        self.reference = reference
        identity = reference.identity if reference is not None else "none"
        version = reference.version if reference is not None else "none"
        super().__init__(
            "Secret provider operation failed "
            f"(failure={failure.value}, provider={provider_id}, "
            f"reference={identity}, version={version})"
        )


@dataclass(frozen=True)
class SecretProviderStatus:
    provider_id: str
    available: bool
    status_code: str


@runtime_checkable
class SecretProvider(Protocol):
    """Startup-oriented provider lifecycle for future local or external adapters."""

    @property
    def provider_id(self) -> str: ...

    def start(self) -> None: ...

    def status(self) -> SecretProviderStatus: ...

    def resolve(self, reference: SecretReference) -> ResolvedSecret: ...

    def close(self) -> None: ...
