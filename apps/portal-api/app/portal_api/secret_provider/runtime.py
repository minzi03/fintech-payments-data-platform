"""Resolve Portal runtime secrets once at startup through a provider contract."""

from __future__ import annotations

import hmac
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from urllib.parse import urlsplit

from pydantic import SecretStr

from portal_api.core.config import PortalApiSettings, PortalEnvironment
from portal_api.secret_provider.environment import (
    EnvironmentSecretEntry,
    EnvironmentSecretProvider,
)
from portal_api.secret_provider.models import (
    ResolvedSecret,
    SecretMetadata,
    SecretPurpose,
    SecretReference,
    SecretValueKind,
)
from portal_api.secret_provider.provider import (
    SecretProvider,
    SecretProviderError,
    SecretProviderFailure,
)

CURRENT_VERSION = "current"
MASTER_KEY_IDENTITY = "portal/security-master-key"


class PortalSecretId(StrEnum):
    DATABASE_URL = "database-url"
    AUDIT_WORKER_DATABASE_URL = "audit-worker-database-url"
    OIDC_CLIENT_SECRET = "oidc-client-secret"
    CLIENT_ADDRESS_HMAC_KEY = "client-address-hmac-key"
    REDIS_URL = "redis-url"
    SECURITY_CURRENT_MASTER_KEY = "security-current-master-key"
    SECURITY_PREVIOUS_MASTER_KEY = "security-previous-master-key"


@dataclass(frozen=True)
class SecretRotationPlan:
    """Explicit current/previous/staged references with no implicit fallback."""

    current: SecretReference | None
    previous: SecretReference | None
    staged_future: SecretReference | None
    transition_started_at: datetime | None
    transition_expires_at: datetime | None

    def __post_init__(self) -> None:
        if self.previous is None:
            if self.transition_started_at is not None or self.transition_expires_at is not None:
                raise ValueError("Key transition timestamps require a previous key reference")
        elif (
            self.current is None
            or self.transition_started_at is None
            or self.transition_expires_at is None
            or self.transition_expires_at <= self.transition_started_at
        ):
            raise ValueError("Previous key reference requires a complete bounded transition")
        versions = tuple(
            reference.version
            for reference in (self.current, self.previous, self.staged_future)
            if reference is not None
        )
        if len(versions) != len(set(versions)):
            raise ValueError("Current, previous, and staged key versions must be distinct")


@dataclass(frozen=True)
class SecretResolutionEvidence:
    """Metadata-only startup evidence suitable for logs and future audit sinks."""

    provider_id: str
    references: tuple[SecretMetadata, ...]
    current_key_version: str | None
    previous_key_version: str | None
    staged_key_version: str | None

    def log_fields(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "references": [
                {
                    "identity": metadata.identity,
                    "version": metadata.version,
                    "purpose": metadata.purpose.value,
                    "value_kind": metadata.value_kind.value,
                }
                for metadata in self.references
            ],
            "current_key_version": self.current_key_version,
            "previous_key_version": self.previous_key_version,
            "staged_key_version": self.staged_key_version,
        }


@dataclass(frozen=True)
class ResolvedPortalSecrets:
    """Startup-resolved values plus non-secret rotation/evidence metadata."""

    _values: Mapping[PortalSecretId, ResolvedSecret]
    rotation: SecretRotationPlan
    evidence: SecretResolutionEvidence

    def __post_init__(self) -> None:
        object.__setattr__(self, "_values", MappingProxyType(dict(self._values)))

    def get(self, secret_id: PortalSecretId) -> ResolvedSecret | None:
        return self._values.get(secret_id)

    def require(self, secret_id: PortalSecretId) -> ResolvedSecret:
        value = self.get(secret_id)
        if value is None:
            raise SecretProviderError(
                SecretProviderFailure.NOT_FOUND,
                provider_id=self.evidence.provider_id,
                reference=None,
            )
        return value

    def require_text(self, secret_id: PortalSecretId) -> str:
        return self.require(secret_id).reveal_text()

    def require_bytes(self, secret_id: PortalSecretId) -> bytes:
        return self.require(secret_id).reveal_bytes()


@dataclass(frozen=True)
class PortalSecretCatalog:
    references: Mapping[PortalSecretId, SecretReference]
    rotation: SecretRotationPlan

    def __post_init__(self) -> None:
        object.__setattr__(self, "references", MappingProxyType(dict(self.references)))


def _text_reference(
    identity: str,
    *,
    purpose: SecretPurpose,
    version: str = CURRENT_VERSION,
) -> SecretReference:
    return SecretReference(
        identity=identity,
        version=version,
        purpose=purpose,
        value_kind=SecretValueKind.TEXT,
    )


def _key_reference(version: str) -> SecretReference:
    return SecretReference(
        identity=MASTER_KEY_IDENTITY,
        version=version,
        purpose=SecretPurpose.SECURITY_MASTER_KEY,
        value_kind=SecretValueKind.BYTES,
        size_bytes=32,
    )


def portal_secret_catalog(settings: PortalApiSettings) -> PortalSecretCatalog:
    references: dict[PortalSecretId, SecretReference] = {}
    current: SecretReference | None = None
    previous: SecretReference | None = None
    staged: SecretReference | None = None

    if settings.security_runtime_enabled:
        references[PortalSecretId.DATABASE_URL] = _text_reference(
            "portal/runtime-database-url",
            purpose=SecretPurpose.DATABASE_CONNECTION,
        )
        references[PortalSecretId.OIDC_CLIENT_SECRET] = _text_reference(
            "portal/oidc-client-secret",
            purpose=SecretPurpose.OIDC_CLIENT_AUTHENTICATION,
        )
        if (
            settings.environment is not PortalEnvironment.TEST
            or settings.security_master_key is not None
        ):
            current = _key_reference(settings.security_key_version)
            references[PortalSecretId.SECURITY_CURRENT_MASTER_KEY] = current
        if settings.security_previous_key_version is not None:
            previous = _key_reference(settings.security_previous_key_version)
            references[PortalSecretId.SECURITY_PREVIOUS_MASTER_KEY] = previous
        if settings.security_future_key_version is not None:
            staged = _key_reference(settings.security_future_key_version)
    elif settings.oidc_client_secret is not None:
        references[PortalSecretId.OIDC_CLIENT_SECRET] = _text_reference(
            "portal/oidc-client-secret",
            purpose=SecretPurpose.OIDC_CLIENT_AUTHENTICATION,
        )

    if settings.abuse_protection_enabled:
        references[PortalSecretId.CLIENT_ADDRESS_HMAC_KEY] = SecretReference(
            identity="portal/client-address-hmac-key",
            version=CURRENT_VERSION,
            purpose=SecretPurpose.ABUSE_CLIENT_FINGERPRINT,
            value_kind=SecretValueKind.BYTES,
            size_bytes=32,
        )
        references[PortalSecretId.REDIS_URL] = _text_reference(
            "portal/redis-url",
            purpose=SecretPurpose.ABUSE_BACKEND_CONNECTION,
        )

    if settings.audit_outbox_enabled:
        references[PortalSecretId.AUDIT_WORKER_DATABASE_URL] = _text_reference(
            "portal/audit-worker-database-url",
            purpose=SecretPurpose.AUDIT_DATABASE_CONNECTION,
        )

    return PortalSecretCatalog(
        references=references,
        rotation=SecretRotationPlan(
            current=current,
            previous=previous,
            staged_future=staged,
            transition_started_at=settings.security_key_transition_started_at,
            transition_expires_at=settings.security_key_transition_expires_at,
        ),
    )


def environment_secret_provider(
    settings: PortalApiSettings,
    catalog: PortalSecretCatalog | None = None,
) -> EnvironmentSecretProvider:
    selected_catalog = catalog or portal_secret_catalog(settings)
    environment_inputs = settings.environment_secret_inputs
    configured: dict[PortalSecretId, SecretStr | None] = {
        PortalSecretId.DATABASE_URL: environment_inputs.database_url,
        PortalSecretId.AUDIT_WORKER_DATABASE_URL: environment_inputs.audit_worker_database_url,
        PortalSecretId.OIDC_CLIENT_SECRET: environment_inputs.oidc_client_secret,
        PortalSecretId.CLIENT_ADDRESS_HMAC_KEY: environment_inputs.client_address_hmac_secret,
        PortalSecretId.REDIS_URL: environment_inputs.redis_url,
        PortalSecretId.SECURITY_CURRENT_MASTER_KEY: environment_inputs.security_master_key,
        PortalSecretId.SECURITY_PREVIOUS_MASTER_KEY: (
            environment_inputs.security_previous_master_key
        ),
    }
    entries: dict[tuple[str, str], EnvironmentSecretEntry] = {}
    for secret_id, reference in selected_catalog.references.items():
        value = configured[secret_id]
        if value is not None:
            entries[(reference.identity, reference.version)] = EnvironmentSecretEntry(
                version=reference.version,
                value=value,
            )
    return EnvironmentSecretProvider(entries)


def resolve_portal_secrets(
    settings: PortalApiSettings,
    provider: SecretProvider,
) -> ResolvedPortalSecrets:
    catalog = portal_secret_catalog(settings)
    if provider.provider_id != settings.secret_provider:
        raise SecretProviderError(
            SecretProviderFailure.PROVIDER_MISMATCH,
            provider_id=provider.provider_id,
        )
    status = provider.status()
    if not status.available:
        raise SecretProviderError(
            SecretProviderFailure.UNAVAILABLE,
            provider_id=provider.provider_id,
        )
    if settings.is_production and provider.provider_id == "environment" and catalog.references:
        raise SecretProviderError(
            SecretProviderFailure.PRODUCTION_RESTRICTED,
            provider_id=provider.provider_id,
        )

    values = {
        secret_id: provider.resolve(reference)
        for secret_id, reference in catalog.references.items()
    }
    _validate_resolved_values(values, provider_id=provider.provider_id)
    current = values.get(PortalSecretId.SECURITY_CURRENT_MASTER_KEY)
    previous = values.get(PortalSecretId.SECURITY_PREVIOUS_MASTER_KEY)
    if (
        current is not None
        and previous is not None
        and hmac.compare_digest(current.reveal_bytes(), previous.reveal_bytes())
    ):
        raise SecretProviderError(
            SecretProviderFailure.CONTINUITY_FAILURE,
            provider_id=provider.provider_id,
            reference=previous.reference,
        )

    metadata = tuple(
        values[secret_id].metadata for secret_id in sorted(values, key=lambda item: item.value)
    )
    rotation = catalog.rotation
    return ResolvedPortalSecrets(
        _values=values,
        rotation=rotation,
        evidence=SecretResolutionEvidence(
            provider_id=provider.provider_id,
            references=metadata,
            current_key_version=rotation.current.version if rotation.current else None,
            previous_key_version=rotation.previous.version if rotation.previous else None,
            staged_key_version=rotation.staged_future.version if rotation.staged_future else None,
        ),
    )


def resolve_environment_secrets(settings: PortalApiSettings) -> ResolvedPortalSecrets:
    """Resolve and close the default provider for standalone adapters and tests."""
    provider = environment_secret_provider(settings)
    provider.start()
    try:
        return resolve_portal_secrets(settings, provider)
    finally:
        provider.close()


def _validate_resolved_values(
    values: Mapping[PortalSecretId, ResolvedSecret],
    *,
    provider_id: str,
) -> None:
    for secret_id in (PortalSecretId.DATABASE_URL, PortalSecretId.AUDIT_WORKER_DATABASE_URL):
        value = values.get(secret_id)
        if value is not None and not value.reveal_text().startswith("postgresql+psycopg://"):
            raise SecretProviderError(
                SecretProviderFailure.INVALID_VALUE,
                provider_id=provider_id,
                reference=value.reference,
            )

    oidc = values.get(PortalSecretId.OIDC_CLIENT_SECRET)
    if oidc is not None and not oidc.reveal_text():
        raise SecretProviderError(
            SecretProviderFailure.INVALID_VALUE,
            provider_id=provider_id,
            reference=oidc.reference,
        )

    redis = values.get(PortalSecretId.REDIS_URL)
    if redis is not None:
        parsed = urlsplit(redis.reveal_text())
        if (
            parsed.scheme not in {"redis", "rediss"}
            or not parsed.hostname
            or parsed.query
            or parsed.fragment
        ):
            raise SecretProviderError(
                SecretProviderFailure.INVALID_VALUE,
                provider_id=provider_id,
                reference=redis.reference,
            )

    for value in values.values():
        if re.search(r"[\r\n]", value.reference.identity):
            raise SecretProviderError(
                SecretProviderFailure.INVALID_VALUE,
                provider_id=provider_id,
                reference=value.reference,
            )
